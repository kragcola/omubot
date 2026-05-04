"""工具基类：定义统一的工具接口。"""

from abc import ABC, abstractmethod
from typing import Any

from services.tools.context import ToolContext


class Tool(ABC):
    """所有工具的基类。子类实现 name / description / parameters / execute。"""

    is_dangerous: bool = False
    """危险工具标记。为 True 时需 admin_approved 上下文才能执行。"""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """JSON Schema 格式的参数定义。"""
        ...

    @abstractmethod
    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        """执行工具，返回文本结果给 LLM。"""
        ...

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
