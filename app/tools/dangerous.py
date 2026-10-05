from app.tools.registry import register_tool
from app.security.policy import ToolPolicy, RiskLevel


@register_tool(
    name="run_arbitrary_shell",
    description="Arbitrary shell execution",
    policy=ToolPolicy(
        name="run_arbitrary_shell",
        risk=RiskLevel.DESTRUCTIVE,
        enabled=False,
        approval_required=True,
        allowed_environments=[],
        allow_arbitrary_input=False,
    ),
)
def run_arbitrary_shell(command: str):

    raise RuntimeError(
        "Arbitrary shell execution is disabled"
    )
