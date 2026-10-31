"""总控台统一错误类型。"""
from __future__ import annotations


class JarvisError(Exception):
    """所有可预期总控台错误的根类型。"""


class ConfigError(JarvisError):
    """配置无效或必要密钥缺失。"""


class LLMError(JarvisError):
    """模型服务请求或协议错误。"""


class ReasoningLeakError(JarvisError):
    """即将发送的 messages 含禁止回传的 reasoning_content。"""


class ToolError(JarvisError):
    """工具参数或执行错误。"""


class ToolNotFound(ToolError):
    """模型请求了未注册或未启用的工具。"""


class PathDenied(ToolError):
    """文件路径不在安全策略允许范围内。"""


class UserCancelled(ToolError):
    """用户取消了破坏性操作。"""


class DependencyMissing(JarvisError):
    """可选依赖缺失，相关能力应局部降级。"""
