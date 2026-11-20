"""Preset MCP server catalog: well-known official/community servers a
user can add in one click. Commands and package names follow each
project's published install instructions. `runtime` names the local
launcher the entry needs (npx / uvx / python); the API reports honestly
whether that launcher exists on this machine instead of letting the
user discover it at connect time.

Secrets are never preset: entries declare env keys with human labels,
the user fills the values, and they are stored as the server's env.
"""
import shlex
import shutil

CATALOG = [
    {
        "key": "filesystem", "name": "文件系统",
        "description": "让员工读写指定目录里的文件，目录之外的路径会被拒绝",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem"],
        "runtime": "npx", "env": [],
        "args_note": "需要在参数最后补上允许访问的目录，例如共享工作区路径",
        "extra_arg_placeholder": "允许访问的目录路径",
    },
    {
        "key": "fetch", "name": "网页抓取",
        "description": "把网页内容取回来转成文字，适合读文档和资讯页",
        "command": "uvx", "args": ["mcp-server-fetch"],
        "runtime": "uvx", "env": [],
    },
    {
        "key": "memory", "name": "记忆",
        "description": "基于知识图谱的长期记忆存取",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"],
        "runtime": "npx", "env": [],
    },
    {
        "key": "sequential-thinking", "name": "分步思考",
        "description": "让模型把复杂问题拆成步骤逐步推理",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
        "runtime": "npx", "env": [],
    },
    {
        "key": "sqlite", "name": "SQLite 数据库",
        "description": "查询和维护一个 SQLite 数据库文件",
        "command": "uvx", "args": ["mcp-server-sqlite", "--db-path"],
        "runtime": "uvx", "env": [],
        "args_note": "需要在参数最后补上数据库文件路径",
        "extra_arg_placeholder": "数据库文件路径",
    },
    {
        "key": "github", "name": "GitHub",
        "description": "读取仓库、议题和拉取请求，需要你的 GitHub 访问令牌",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
        "runtime": "npx",
        "env": [{"key": "GITHUB_PERSONAL_ACCESS_TOKEN", "label": "GitHub 访问令牌", "secret": True}],
    },
    {
        "key": "brave-search", "name": "Brave 网页搜索",
        "description": "用 Brave 搜索引擎查网页，需要 Brave 搜索的接口密钥",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-brave-search"],
        "runtime": "npx",
        "env": [{"key": "BRAVE_API_KEY", "label": "Brave 搜索接口密钥", "secret": True}],
    },
    {
        "key": "example-echo", "name": "示例工具服务",
        "description": "随项目源码附带的本地示例，提供回显与加法两个工具，适合先验证整条链路",
        "command": "python3", "args": [],
        "runtime": "python3", "env": [], "local_example": True,
        "args_note": "需要在参数里补上示例脚本的完整路径，源码包里在 examples/mcp-server/server.py",
        "extra_arg_placeholder": "示例脚本完整路径",
    },
]


def catalog_with_availability():
    out = []
    for entry in CATALOG:
        item = dict(entry)
        found = shutil.which(entry["runtime"])
        item["runtime_available"] = bool(found)
        item["runtime_path"] = found or ""
        if not found:
            item["runtime_missing_hint"] = (
                f"这台电脑还没有 {entry['runtime']}，装好之后才能连接这台服务器")
        out.append(item)
    return out


def compose_command(entry, extra_arg: str = "") -> str:
    parts = [entry["command"], *entry["args"]]
    if extra_arg.strip():
        parts.append(extra_arg.strip())
    return " ".join(shlex.quote(p) if any(c in p for c in " \t\"'") else p
                    for p in parts)
