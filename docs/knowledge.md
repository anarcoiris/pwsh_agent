# Arquitectura de Agente Local Multi-Modelo
## Knowledge Base — Diseño, Principios y Configuraciones

> Compilación de hallazgos, análisis y decisiones de diseño extraídos de la conversación de diseño arquitectónico para un agente local basado en modelos LLM cuantizados sobre hardware Pascal.

---

## 1. Contexto del Hardware

### 1× GTX 1080 + 2× GTX 1070 (Pascal, 8 GB each)

| Parámetro | Valor |
|---|---|
| VRAM por GPU | 8 GB (1080: GDDR5X; 1070: GDDR5) |
| VRAM total | 24 GB |
| Arquitectura | Pascal |
| Tensor Cores | No |
| NVLink | No |
| Interconexión GPU–GPU | PCIe 3.0 x16 (~12–14 GB/s por dirección) |
| Rendimiento relativo | GTX 1080 ~20–35% más rápida que GTX 1070 en inferencia |

**Implicación crítica del hardware:** La ausencia de NVLink hace que el *model parallelism* sea impracticable. El patrón correcto es **paralelismo de especialización**: un modelo completo por GPU, sin tensor-split.

**Asignación multi-GPU (Escenario 1 actualizado):**

| GPU | Modelo | Rol | Endpoint |
|---|---|---|---|
| GTX 1070 #1 | chat-analyzer | INTAKE | `:11436` |
| GTX 1080 | vibethinker:3b | PLAN + EVALUATE | `:11434` |
| GTX 1070 #2 | qwen2.5-coder:7b | VALIDATE + EXECUTE | `:11435` |

RAG, FAISS, embeddings y FSM del orquestador viven en **CPU** (Fase 1).

---

## 2. Stack de Modelos

| Modelo | Cuantización | VRAM pesos | Rol en pipeline |
|---|---|---|---|
| Qwen2.5-7B-Instruct | Q4_K_M | ~4.5 GB | Intake Agent |
| VibeThinker-3B | Q4_K_M | ~2.0 GB | Planner / Reasoning Agent |
| Qwen2.5-Coder-7B-Instruct | Q4_K_M | ~4.5 GB | Code Agent |
| Qwen2.5-1.5B | Q4_K_M | ~1.0 GB | Draft model (especulativo) |
| Qwen2.5-14B-Instruct | Q4_K_M | ~8.5 GB | Planner avanzado (split GPU 1+2) |
| bge-m3 | FP16 | ~1.5 GB | Embeddings / RAG (GPU dedicada) |

### KV Cache — estimación para Qwen2.5-7B con GQA

Qwen2.5-7B usa Grouped Query Attention (GQA): 28 capas, 28 attention heads, **4 KV heads**, 128 head_dim.

```
KV cache por token = 2 (K+V) × 4 KV heads × 128 head_dim × 28 capas × 2 bytes (FP16)
                   = 57,344 bytes ≈ 56 KB por token

A 8,192 tokens  → ~448 MB
A 4,096 tokens  → ~224 MB
A 32,768 tokens → ~1.79 GB

Con cuantización Q4_0 del KV cache:
A 32,768 tokens → ~0.45 GB  (≡ FP16 a 8k tokens)
```

**Estimación VRAM total a 4k contexto (configuración base):**

```
GPU 1 (Intake, chat-analyzer @ :11436):
  Pesos:     ~4.5 GB
  KV cache:  ~0.22 GB @ 4k
  Total:     ~5.2 GB  ← cabe en 8 GB

GPU 2 (Planner, VibeThinker 3B @ :11434, GTX 1080):
  Pesos:     ~2.0 GB
  KV cache:  ~0.1 GB
  Total:     ~2.4 GB  ← cabe holgado

GPU 3 (Coder, Qwen Coder 7B @ :11435):
  Pesos:     ~4.5 GB
  KV cache:  ~0.22 GB @ 4k
  Total:     ~5.2 GB  ← cabe en 8 GB
```

> **Aviso:** La ejecución simultánea a num_ctx=8,192 es borderline. La afirmación inicial de "encajar ambos modelos en 8 GB" era optimista al no contabilizar activaciones, workspace de operadores CUDA (~100–300 MB) y overhead del driver (~100–200 MB). El margen real es ajustado. Preferir num_ctx=4,096 para mayor estabilidad.

---

## 3. Pipeline de Cinco Etapas

```
Usuario
  │
  ▼  [entrada libre]
Intake Agent         ← GPU 1 · Qwen 7B
  │
  ▼  [JSON estructurado]
Task Graph / Orquestador  ← CPU
  │
  ▼  [JSON]
Planner / Reasoning Agent ← GPU 2 · VibeThinker-3B
  │
  ▼  [Plan JSON]
  ├─► Code Agent      ← GPU 3 · Qwen Coder 7B
  ├─► Review Agent    ← CPU
  └─► Search Agent    ← CPU + RAG
```

### Handoffs entre etapas

Los handoffs entre agentes deben ser **JSON estructurado**, no texto libre. El texto libre genera:
- Redundancia semántica (cada modelo reformula lo anterior)
- Latencia de parseo (texto no estructurado requiere comprensión, no solo lectura)
- Amplificación de errores (ambigüedades se propagan)
- Contaminación del contexto siguiente

**Esquema de handoff recomendado:**

```json
{
  "goal": "descripción del objetivo",
  "constraints": ["restricción 1", "restricción 2"],
  "unknowns": ["ambigüedad 1"],
  "artifacts": ["archivo.ts", "config.json"],
  "risks": [{"item": "...", "level": "low|medium|high"}],
  "tasks": [
    {"id": 1, "action": "...", "file": "..."}
  ]
}
```

---

## 4. Los 10 Hallazgos de Diseño

### Hallazgo 1 — Error de categoría: system prompt ≠ orquestador

El system prompt opera como un prefijo de contexto persistente que desplaza la distribución de salida del modelo. Carece de tres propiedades esenciales para orquestar flujos:

- **Estado mutable:** no puede actualizar variables ni registrar progreso
- **Ramas condicionales:** no puede evaluar condiciones en tiempo de inferencia
- **Iteración:** no puede hacer loops sobre resultados anteriores

Mezclar política global (qué puede hacer el modelo) con lógica de orquestación (cuándo y cómo hacerlo) en un mismo artefacto crea acoplamiento entre capas con ciclos de cambio radicalmente distintos.

**Conclusión:** El system prompt debe contener únicamente invariantes: rol, límites de seguridad, estilo de salida. La lógica de flujo pertenece al controlador externo en código.

---

### Hallazgo 2 — Arquitectura de cuatro capas

La separación correcta es isomorfa al principio de *Separation of Concerns* (Dijkstra, 1974) y al patrón *Layered Architecture*:

| Capa | Artefacto | Contenido | Frecuencia de cambio |
|---|---|---|---|
| Política global | System prompt | Rol, límites, formato de salida | Raramente |
| Orquestador | Código (FSM) | Estado actual, transiciones | Por tarea |
| Skill activa | Llamada de inferencia | Inspección · Plan · Gen · Val · Rollback | Por paso |
| Contrato de salida | JSON schema | Campos requeridos por el consumidor downstream | Por handoff |

Cada capa tiene interfaz bien definida hacia arriba y hacia abajo, y es testeable de forma independiente.

---

### Hallazgo 3 — Forzar JSON en la fase de razonamiento penaliza la calidad

Los estudios de Chain-of-Thought (Wei et al., 2022; Wang et al., 2023) demuestran que permitir razonamiento intermedio en lenguaje natural antes de formatear mejora el rendimiento en tareas de planificación. Restringir el espacio de salida a JSON durante el razonamiento:

1. Elimina la posibilidad de scratchpad intermedio
2. Puede causar errores de formato que interrumpen el parsing
3. Reduce la coherencia interna de planes complejos

**Conclusión:** El formato JSON es apropiado en el **contrato de salida** (capa 4), no durante el proceso de razonamiento interno del modelo (capas 2–3). Separar el paso de razonamiento (salida libre) del paso de serialización (transform ligero a JSON).

---

### Hallazgo 4 — Correspondencia tarea–tamaño de modelo

Los modelos de 3B parámetros son fiables para:
- Clasificación y routing de intenciones
- Extracción de entidades y relaciones
- Completado de plantillas simples
- Resumen de contexto corto

**No** son fiables para:
- Planificación multi-paso con dependencias complejas
- Razonamiento sobre efectos secundarios de cambios de código
- Generación de planes con condiciones de rollback no triviales

**Conclusión:** El Planner pequeño (3B) debe ser un **clasificador con contexto y router de skills**, no un oráculo de planificación profunda. Asignarlo a "planificación general" sobreestima su capacidad real.

---

### Hallazgo 5 — Restricciones de VRAM: estimación correcta

**La afirmación "encajar ambos modelos en 8 GB con contexto moderado" era optimista.** Una estimación rigurosa debe incluir:

```
VRAM total = pesos + KV cache + activaciones + workspace CUDA + overhead driver/SO
```

Los ítems que se omiten habitualmente:
- Activaciones durante inferencia: ~200–400 MB por paso forward
- Workspace de operadores CUDA: ~100–300 MB
- Overhead del driver y SO en GPU: ~100–200 MB

A num_ctx=4,096 la ejecución simultánea es borderline pero posible. A num_ctx=8,192 es inviable en la misma GPU. La recomendación conservadora es carga secuencial o num_ctx≤4,096.

---

### Hallazgo 6 — La máquina de estados finitos (FSM) es la abstracción de control correcta

Un agente de código local es formalmente un FSM con:

- **Estados:** inspección, planificación, generación, validación, rollback, aprobación humana
- **Eventos:** resultado de test, presencia de diff, flag de riesgo, timeout
- **Función de transición:** determinista o estocástica según el resultado de cada skill
- **Estados de aceptación:** tarea completada, rollback exitoso
- **Estados de error:** intervención humana requerida

Esta abstracción tiene propiedades verificables: decidibilidad, trazabilidad por log de estados, debugging determinista. Los sistemas de agentes LLM sin estructura FSM tienen propiedades impredecibles y no son auditables.

**Conclusión:** El controlador del agente debe implementarse como FSM explícita en código. Los modelos LLM son llamadas de inferencia dentro de las transiciones, no el controlador en sí.

---

### Hallazgo 7 — Contexto ≠ Memoria: el anti-patrón más costoso

El contexto de un LLM es un **buffer de entrada efímero** para un único paso de inferencia. La memoria es **estado persistente y direccionable**. Confundirlos produce:

- Crecimiento lineal (o superlineal) de tokens por paso
- Latencia de prefilling cuadrática: O(n²) en atención estándar para n tokens
- Contaminación de contexto: el modelo procesa información ya obsoleta
- Pérdida de coherencia en cadenas largas (*needle-in-haystack problem*)

**Anti-patrón documentado:**
```
Prompt + Historial + Resumen + Plan + Monólogo + Resultados → 30,000–50,000 tokens
```

**Patrón correcto:**
```
Estado estructurado actual (CPU) + subtarea actual → 1,000–4,000 tokens por llamada
```

**Conclusión:** Mantener el estado como grafo estructurado en CPU. Inyectar al modelo únicamente el fragmento pertinente a cada subtarea.

---

### Hallazgo 8 — Handoff JSON como contrato de interfaz entre agentes

La cadena texto → texto → texto entre agentes genera redundancia semántica, latencia de parseo, amplificación de errores y contaminación del contexto siguiente. Los handoffs JSON estructurados resuelven los cuatro problemas simultáneamente.

Este es el **Principio de Contrato Explícito** aplicado a sistemas multi-modelo: la interfaz entre componentes debe ser más estable y formal que la implementación interna de cada uno. Tiene precedentes directos en diseño de microservicios y protocolos de comunicación.

**Nota importante:** El JSON no debe forzarse durante el razonamiento interno del modelo (véase Hallazgo 3). Solo en los puntos de handoff entre modelos. Son dos momentos distintos del pipeline.

---

### Hallazgo 9 — Pascal excluye model parallelism; el paralelismo correcto es por especialización

**Model parallelism en Pascal (sin NVLink):**
- PCIe 3.0 x16: ~12–14 GB/s por dirección
- Para un modelo de 70B con 80 capas, cada capa transfiere ~128 MB de activaciones
- A 14 GB/s: ~9 ms por capa solo en transferencia vs ~2–5 ms de cómputo real
- El bus se convierte en el cuello de botella dominante

**Paralelismo de especialización (correcto para Pascal):**
- Un modelo completo por GPU
- GPUs completamente independientes durante inferencia
- Sincronización solo mediante JSON en handoffs (kilobytes, no gigabytes)
- Sin transferencia de tensores entre GPUs

**Conclusión:** 3× GTX 1080 = 3 agentes especializados concurrentes. No 1 modelo grande distribuido.

---

### Hallazgo 10 — CPU como capa de infraestructura, no cómputo de segundo nivel

| Componente | Hardware óptimo | Razón |
|---|---|---|
| Grafos de tareas (FSM) | CPU | O(1)–O(log n), no matricial |
| Índices vectoriales (FAISS, HNSWLIB) | CPU | Muy optimizados con SIMD |
| KV store / estado estructurado | CPU RAM | I/O bound, no compute bound |
| Rollback git | CPU | I/O bound |
| Embeddings para RAG | GPU dedicada o CPU | Depende del throughput requerido |
| Inferencia de matrices (atención, FFN) | GPU | Ventaja 10–50× sobre CPU |

**Conclusión:** Reservar las GPUs exclusivamente para multiplicaciones matriciales. Delegar estado, grafos, índices y rollback a CPU. Esta no es una concesión de diseño sino la asignación óptima de recursos.

---

## 5. CPU State Manager

El orquestador en CPU es la pieza central del sistema. Sus responsabilidades son:

```
CPU State Manager
├── Task Graph        → grafo de tareas pendientes, en curso, completadas
├── Estado actual     → fase del pipeline, historial de resultados
├── Memoria           → resultados previos relevantes (NO en contexto del LLM)
├── RAG / Índice      → embeddings, búsqueda vectorial (FAISS/HNSWLIB)
├── Router de skills  → decide qué skill activar según estado
└── Rollback          → interfaz con git para revertir cambios
```

El State Manager consume JSON de cada agente y produce JSON para el siguiente, nunca texto libre.

---

## 6. Configuraciones GPU — Cuatro Escenarios

### Escenario 1: Base (referencia)

| GPU | Modelo | Rol | VRAM |
|---|---|---|---|
| GPU 1 | Qwen2.5-7B | Intake Agent | ~4.5 GB |
| GPU 2 | VibeThinker-3B | Planner | ~2.0 GB |
| GPU 3 | Qwen2.5-Coder-7B | Code Agent | ~4.5 GB |

---

### Escenario 2: Decodificación especulativa

**Aplicación:** maximizar throughput de generación de código.

| GPU | Modelo | Rol | VRAM |
|---|---|---|---|
| GPU 1 | Qwen2.5-Coder-7B | Target model (verifica) | ~4.5 GB |
| GPU 2 | Qwen2.5-1.5B | Draft model (propone k tokens) | ~1.0 GB |
| GPU 3 | VibeThinker-3B | Planner (libre en paralelo) | ~2.0 GB |

**Mecanismo:**
1. Draft (1.5B) genera k=5–10 tokens candidatos en un paso muy rápido
2. Target (Coder 7B) verifica los k tokens en **un único forward pass** (vs k pases normalmente)
3. Para código, donde las secuencias son predecibles, el throughput efectivo mejora **2–3×**

**Condición necesaria:** draft y target deben compartir vocabulario y tokenizador. La familia Qwen2.5 (0.5B, 1.5B, 3B, 7B, 14B) lo cumple, haciéndola especialmente adecuada para este patrón.

**Flag llama.cpp:** `--model-draft <ruta_draft> --draft-max 10 --draft-min 5`

---

### Escenario 3: Modelo 14B dividido entre GPU 1 y GPU 2

**Aplicación:** mayor capacidad de razonamiento en el Planner sin sacrificar el Code Agent.

| GPU | Modelo | Rol | VRAM |
|---|---|---|---|
| GPU 1 | Qwen2.5-14B (parte A) | Planner avanzado — capas primera mitad | ~4.5 GB |
| GPU 2 | Qwen2.5-14B (parte B) | Planner avanzado — capas segunda mitad | ~4.5 GB |
| GPU 3 | Qwen2.5-Coder-7B o VibeThinker-3B | Code Agent o Planner ligero | ~4.5 GB |

**Penalización PCIe:** la transferencia de activaciones en la frontera de capas introduce ~10–30 ms de latencia por token. Para tareas de planificación (donde la calidad importa más que la velocidad de generación), este coste es asumible.

**Flag llama.cpp:** `--tensor-split 1,1,0` (dividir 14B entre GPU 0 y GPU 1 en partes iguales)

> **Nota sobre Qwen2.5-14B:** arquitectura típica ~48 capas. Con tensor-split 1,1 cada GPU aloja ~24 capas. KV cache distribuido proporcionalmente.

---

### Escenario 4: GPU dedicada a embeddings / RAG

**Aplicación:** búsqueda semántica sin competir por ciclos de inferencia.

| GPU | Modelo | Rol | VRAM |
|---|---|---|---|
| GPU 1 | Qwen2.5-7B | LLM principal (Intake + Coder, secuencial) | ~4.5 GB |
| GPU 2 | VibeThinker-3B | Planner | ~2.0 GB |
| GPU 3 | bge-m3 | Embedding engine — RAG en tiempo real | ~1.5 GB |

**Ventaja:** bge-m3 fijo en GPU 3 (~1.5 GB de pesos, ~6.5 GB libres para índice activo) sirve consultas de similitud semántica en milisegundos de forma continua, con FAISS o HNSWLIB en CPU coordinando el índice. Elimina el cuello de botella que aparece cuando embedding y generación comparten GPU.

---

## 7. Estrategias de KV Cache

### Opción A: KV cuantizado (nativo en llama.cpp)

La más práctica. No requiere cambios de hardware ni framework.

```bash
--cache-type-k q4_0 --cache-type-v q4_0
```

Efecto: reduce el KV cache a ~25% del tamaño FP16. Para Qwen2.5-7B con GQA:

| Contexto | KV FP16 | KV Q4_0 |
|---|---|---|
| 8,192 tokens | ~448 MB | ~112 MB |
| 32,768 tokens | ~1,792 MB | ~448 MB |
| 65,536 tokens | ~3,584 MB | ~896 MB |

Un contexto de 32k tokens en Q4_0 cuesta lo mismo en VRAM que FP16 a 8k. Pérdida de precisión marginal en contextos muy largos.

### Opción B: Layer split desequilibrado (llama.cpp)

Si se divide el modelo con `--tensor-split 2,1,0`, GPU 2 recibe menos capas y le sobra VRAM para KV de esas capas. No es KV dedicado puro pero el efecto es similar.

### Opción C: KV paginado en múltiples dispositivos (vLLM / SGLang)

Frameworks con PagedAttention pueden enrutar páginas de KV a dispositivos distintos, habilitando verdadero "KV cache GPU dedicada". Requiere migrar de llama.cpp a vLLM o SGLang. Útil si el pipeline requiere contextos de 64k–128k tokens de forma sostenida.

---

## 8. Combinaciones Adicionales

### LoRA hot-swap

- Modelo base congelado en GPU 1 (o repartido en GPU 1+2)
- Adaptadores LoRA cargados desde CPU RAM según la tarea activa
- Un adapter LoRA con r=64 pesa ~50–200 MB
- Tiempo de carga desde CPU RAM: orden de milisegundos
- Permite especialización por dominio (PowerShell, revisión de código, documentación) sin recargar el modelo base

### Batching continuo duplicado

- Dos instancias del mismo Coder 7B en GPU 1 y GPU 3
- Planner en GPU 2 distribuye subtareas entre ambas mediante cola de trabajo
- Duplica el throughput de generación de código
- A costa de fijar GPU 3 en el mismo rol que GPU 1

### Fine-tuning incremental online

- GPU 3 corre LoRA training con batch pequeño (r=8, batch=1–2) sobre los errores recientes del pipeline
- GPU 1 y GPU 2 hacen inferencia en paralelo
- Permite que el sistema mejore sobre sus propios fallos de forma continua
- Requiere gestión de memoria cuidadosa para no interferir con inferencia activa

---

## 9. Tabla de Síntesis — Escenarios y Combinaciones

| Escenario / Técnica | Ganancia principal | Limitación | Framework |
|---|---|---|---|
| Base (3 agentes) | Claridad y modularidad | Ningún modelo supera 7B | llama.cpp |
| Especulativo | 2–3× throughput en código | Modelos deben ser de misma familia | llama.cpp |
| Split 14B | Mayor capacidad de razonamiento | Latencia PCIe en frontera de capas | llama.cpp |
| RAG GPU dedicada | Búsqueda semántica siempre activa | GPU 3 no hace inferencia LLM | llama.cpp |
| KV cuantizado Q4 | 4× contexto sin VRAM extra | Pérdida marginal de precisión en ctx largo | llama.cpp |
| LoRA hot-swap | Especialización sin recarga | Latencia de carga (ms) por cambio de tarea | llama.cpp |
| Batching continuo | Mayor throughput paralelo | Necesita orquestador de cola de tareas | llama.cpp |
| Fine-tuning online | Aprendizaje de errores propios | Gestión delicada de memoria GPU | llama.cpp / PyTorch |
| KV dedicado (vLLM) | Contextos 64k–128k sostenidos | Requiere migrar de llama.cpp | vLLM / SGLang |

**Selección según cuello de botella:**
- Velocidad de generación de código → **Especulativo**
- Calidad del plan → **Split 14B**
- Latencia de búsqueda contextual → **RAG GPU**
- Adaptabilidad a distintos tipos de tarea → **LoRA hot-swap**
- Contexto muy largo → **KV cuantizado o vLLM**

---

## 10. Principios de Diseño — Síntesis Final

### Principio 1: Correspondencia de capa

Cada tipo de lógica tiene su artefacto correcto. No mezclar capas en el mismo artefacto.

```
Invariantes del modelo  → System prompt
Lógica de flujo         → Código (FSM)
Razonamiento / skill    → Llamada de inferencia LLM
Serialización           → JSON schema en handoff
Estado histórico        → CPU State Manager (no contexto del LLM)
```

### Principio 2: Contexto mínimo por inferencia

El modelo recibe únicamente: **estado estructurado actual** + **subtarea actual**.  
La memoria histórica, el grafo de tareas y los resultados previos viven en CPU, no en el contexto.

```
Anti-patrón: prompt + historial + resumen + plan + monólogo + resultados → 30-50k tokens
Correcto:    estado JSON (CPU) + subtarea → 1-4k tokens por llamada
```

### Principio 3: Paralelismo de especialización sobre model parallelism

En hardware Pascal sin NVLink, cada GPU ejecuta un agente especializado independiente. La comunicación entre agentes es JSON (KB), no transferencia de tensores (GB).

```
Incorrecto:  1 modelo 70B distribuido entre 3 GPUs (cuello PCIe)
Correcto:    3 modelos especializados concurrentes, cada uno en su GPU
```

### Principio 4: Partición CPU/GPU por tipo de operación

```
GPU = multiplicaciones matriciales (atención, FFN, embeddings densos)
CPU = estado estructurado, grafos de tareas, índices vectoriales, rollback
```

No es una concesión de diseño. Es la asignación óptima de recursos para cada tipo de operación.

---

## 11. Notas de Implementación — Flags llama.cpp

```bash
# Configuración base por modelo
--n-gpu-layers 999           # todos los layers en GPU
--ctx-size 4096              # contexto conservador para Pascal 8GB
--batch-size 512
--threads 8                  # ajustar según CPU

# KV cache cuantizado (recomendado)
--cache-type-k q4_0
--cache-type-v q4_0

# Split de modelo entre dos GPUs (ej: 14B en GPU0+GPU1)
--tensor-split 1,1,0         # proporciones: GPU0, GPU1, GPU2

# Decodificación especulativa
--model-draft <ruta_1.5B>
--draft-max 10
--draft-min 5
--draft-p-min 0.8

# Temperatura para Planner (razonamiento determinista)
--temp 0.2
--top-p 0.85
--top-k 20
--repeat-penalty 1.1

# Temperatura para Code Agent (generación creativa baja)
--temp 0.1
--top-p 0.95
```

---

## 12. Nomenclatura de Agentes (Referencia)

| Nombre informal | Nombre formal equivalente | Función |
|---|---|---|
| chat-analyzer | Intake Agent / Request Understanding Agent | Normaliza, contextualiza, detecta objetivos y ambigüedades |
| VibeThinker (planner) | Planner / Reasoning Agent / Task Decomposition Agent | Expande objetivos, genera subtareas, enruta skills |
| Qwen Coder | Execution Agent / Implementation Agent | Genera parches, diffs, scripts |
| (implícito) | Validation Agent | Verifica tests, lint, coherencia |
| (implícito) | Rollback Agent | Revertir cambios vía git si falla validación |

---

*Documento generado a partir de análisis conversacional de arquitectura.*  
*Modelos de referencia: Qwen2.5 family, VibeThinker-3B-Q4_K_M-GGUF.*  
*Hardware de referencia: 3× GTX 1080 8GB (Pascal, PCIe 3.0, sin NVLink).*
