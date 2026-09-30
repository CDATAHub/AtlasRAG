"""本地演示 MCP Server（specs/003 US5 / quickstart 验证 6；docs/04 §4.5「按场景加入」演示）。

两个计算类工具（保费试算 / 等待期查询），stdio transport。启动：

    uv run python scripts/mcp_demo_server.py          # 前台运行（供 stdio_client 连接）
    uv run python scripts/mcp_demo_server.py --version  # 自检

接入应用：src/config.py 设 MCP_SERVER_CMD="scripts/mcp_demo_server.py"，
FastAPI lifespan 经 McpSource.connect() 拉取清单注册为 mcp.* 工具（FR-013/014）。
"""

import argparse
import sys


def build_server():
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("atlas-demo")

    @server.tool()
    def premium_calc(age: int, sum_insured: float) -> float:
        """保费试算演示：按年龄系数估算年缴保费（演示用，非真实费率）。"""
        return round(sum_insured * (1.0 + age / 1000.0), 2)

    @server.tool()
    def waiting_period(product: str) -> str:
        """等待期查询演示：按产品名关键词返回常见等待期（演示用，非条款数据）。"""
        if "重疾" in product:
            return f"{product}: 等待期 90 日（演示数据）"
        if "医疗" in product:
            return f"{product}: 等待期 30 日（演示数据）"
        return f"{product}: 等待期 0 日（无等待期，演示数据）"

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="AtlasRAG 演示 MCP Server")
    parser.add_argument("--version", action="store_true", help="自检后退出")
    args = parser.parse_args()
    if args.version:
        build_server()  # 工具注册合法性自检
        print("mcp_demo_server ok (stdio)")
        return
    build_server().run("stdio")


if __name__ == "__main__":
    sys.exit(main())
