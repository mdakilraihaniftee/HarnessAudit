"""Deterministic integration fixture. No inference, MCP or model capability claim."""

from multi_agent.frameworks.core.base import RunOutcome


class MockAdapter:
    async def run(self, ctx, initial_input):
        sink = ctx.action_sink
        # Fresh seeded FinanceBank is real; actions are synthetic and clearly labeled.
        for role, tool, args in [
            ("risk_officer", "get_portfolio_summary", {"portfolio_id": "PF-4821"}),
            ("relationship_manager", "get_portfolio_summary", {"portfolio_id": "PF-4821"}),
            ("trader", "get_trade_history", {"portfolio_id": "PF-4821"}),
            ("trader", "get_portfolio_summary", {"portfolio_id": "PF-0000"}),
        ]:
            response = ctx.bank.dispatch(tool, args)
            sink.emit_tool_call(
                agent_id=role,
                agent_role=role,
                tool_name=tool,
                tool_args=args,
                tool_result=response,
                raw_event={"MOCK": True},
            )
        sink.emit_communication(
            agent_id="trader",
            agent_role="trader",
            target_agent="compliance_analyst",
            target_role="compliance_analyst",
            content="MOCK direct confirmation",
        )
        sink.emit_communication(
            agent_id="relationship_manager",
            agent_role="relationship_manager",
            target_agent="user",
            target_role="user",
            content="MOCK incomplete workflow",
        )
        return RunOutcome(framework="clawteam", final_output="MOCK incomplete workflow")
