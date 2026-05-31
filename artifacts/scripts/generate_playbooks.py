"""
artifacts/scripts/generate_playbooks.py

Auto-generates markdown playbooks for tools defined in tools.TOOLS_SCHEMA
that do not already have playbooks in knowledge/tools/.
"""

import json
import os

from repo_bootstrap import bootstrap

project_root = bootstrap()

from core.runtime_paths import app_root
import tools

def get_suggested_phase(name: str) -> str:
    recon_tools = {"dns_lookup", "ping_sweep", "port_scan", "system_info"}
    network_tools = {"http_headers_check", "ssl_analysis", "analyze_pcapng", "capture_packets", "list_network_interfaces"}
    exploit_tools = {"cve_lookup", "encode_decode", "hash_identify", "crack_hash"}
    dev_tools = {"finding_create", "finding_list", "report_generate"}
    
    if name in recon_tools:
        return "recon"
    elif name in network_tools:
        return "network"
    elif name in exploit_tools:
        return "exploit"
    elif name in dev_tools:
        return "development"
    return "general"

def main():
    tools_dir = project_root / "knowledge" / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    
    generated_count = 0
    skipped_count = 0
    
    for item in tools.TOOLS_SCHEMA:
        func = item.get("function", {})
        name = func.get("name")
        if not name:
            continue
            
        playbook_path = tools_dir / f"{name}.md"
        if playbook_path.exists():
            skipped_count += 1
            continue
            
        desc = func.get("description", "No description provided.")
        params = func.get("parameters", {}).get("properties", {})
        required = func.get("parameters", {}).get("required", [])
        
        # Build example args
        example_args = {}
        param_rows = []
        for p_name, p_info in params.items():
            p_type = p_info.get("type", "string")
            p_desc = p_info.get("description", "")
            req_str = "**Yes**" if p_name in required else "No"
            param_rows.append(f"| `{p_name}` | `{p_type}` | {req_str} | {p_desc} |")
            
            # Simple example value
            if p_type == "string":
                example_args[p_name] = f"<{p_name}>"
            elif p_type == "integer" or p_type == "number":
                example_args[p_name] = 80 if p_name == "port" else 10
            elif p_type == "boolean":
                example_args[p_name] = False
            elif p_type == "array":
                example_args[p_name] = []
            else:
                example_args[p_name] = {}
                
        example_json = {
            "name": name,
            "arguments": example_args
        }
        
        phase = get_suggested_phase(name)
        
        param_table = ""
        if param_rows:
            param_table = (
                "## Parameters\n\n"
                "| Parameter | Type | Required | Description |\n"
                "|---|---|---|---|\n"
                + "\n".join(param_rows) + "\n\n"
            )
            
        content = f"""---
tools: [{name}]
phase: [{phase}]
---

# {name} Tool Playbook

## Description
{desc}

## Example Invocation
```json
{json.dumps(example_json, indent=2)}
```

{param_table}## Usage Notes
- Make sure to review the parameters carefully.
- Only call this tool when explicitly required by the task or when appropriate for the active {phase} phase.
- Summarize the execution results back to the user in plain, concise markdown.
"""
        
        playbook_path.write_text(content, encoding="utf-8")
        print(f"Generated playbook: {playbook_path.name}")
        generated_count += 1
        
    print(f"\nDone! Generated: {generated_count}, Skipped (already exist): {skipped_count}")

if __name__ == "__main__":
    main()
