import os
import sys
from mcp.server.fastmcp import FastMCP
import tools

# Instantiate the FastMCP server
mcp = FastMCP("PowerShell_Agent_MCP")

# Instance of SequentialThinkingEngine for the MCP session
thinking_engine = tools.SequentialThinkingEngine()

@mcp.tool()
def sequentialthinking(
    thought: str, 
    nextThoughtNeeded: bool, 
    thoughtNumber: int, 
    totalThoughts: int, 
    isRevision: bool = False, 
    revisesThought: int = None
) -> dict:
    """
    A detailed tool for dynamic and reflective problem-solving through sequential thoughts.
    Useful for breaking down complicated tasks into manageable logical units.
    """
    return thinking_engine.process_thought({
        "thought": thought,
        "nextThoughtNeeded": nextThoughtNeeded,
        "thoughtNumber": thoughtNumber,
        "totalThoughts": totalThoughts,
        "isRevision": isRevision,
        "revisesThought": revisesThought
    })

@mcp.tool()
def host_exec(command: str, timeout: int = 120) -> dict:
    """
    Executes a shell command directly on the Windows host machine using PowerShell.
    Returns a structured payload containing the exit code, stdout, stderr, and elapsed duration.
    """
    return tools.host_exec(command=command, timeout=timeout)

@mcp.tool()
def read_file(path: str) -> dict:
    """
    Reads the text contents of a local file from the system.
    """
    return tools.read_file(path=path)

@mcp.tool()
def write_file(path: str, content: str) -> dict:
    """
    Writes text content directly to a local file, creating parent folders if needed.
    """
    return tools.write_file(path=path, content=content)

@mcp.tool()
def list_network_interfaces() -> dict:
    """
    Lists all available local network interfaces with tshark.
    """
    return tools.list_network_interfaces()

@mcp.tool()
def capture_packets(interface: str = "1", duration: int = 10, output_path: str = None) -> dict:
    """
    Captures live packets on a specified network interface for a set duration.
    """
    return tools.capture_packets(interface=interface, duration=duration, output_path=output_path)

@mcp.tool()
def analyze_pcapng(file_path: str, filter_expression: str = None, limit: int = 50) -> dict:
    """
    Analyzes an existing pcapng/pcap file using tshark to yield protocol stats, network conversations, potential plaintext passwords, and custom filtered logs.
    """
    return tools.analyze_pcapng(file_path=file_path, filter_expression=filter_expression, limit=limit)

@mcp.tool()
def crack_hash(
    target_hash: str,
    mask: str = "NNNNNNAA!",
    salt: str = None,
    min_len: int = None,
    known_prefix: str = None,
    known_suffix: str = None,
    wordlist: str = None,
    cpu_workers: int = None,
    no_gpu: bool = False,
    timeout: int = 1200
) -> dict:
    """
    Cracks a SHA-256 target hash using the high-performance CPU+GPU hash_pro7.py tool via a robust non-interactive runner.
    """
    return tools.crack_hash(
        target_hash=target_hash,
        mask=mask,
        salt=salt,
        min_len=min_len,
        known_prefix=known_prefix,
        known_suffix=known_suffix,
        wordlist=wordlist,
        cpu_workers=cpu_workers,
        no_gpu=no_gpu,
        timeout=timeout
    )

if __name__ == "__main__":
    # Start the FastMCP stdio server
    mcp.run(transport="stdio")
