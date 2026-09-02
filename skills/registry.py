# ============================================================
# Skill 注册表（skills/registry.py）
# M1 Skill 化 · 两级渲染（渐进式披露）
# - register(skill)  : 注册，重名抛异常
# - get(name)        : 取单个技能
# - list_all()       : 紧凑单行清单（第一级：description 层，省 token）
# - get(name).to_manifest() : 完整详情（第二级：SKILL.md 层）
# - 零第三方依赖
# ============================================================

from typing import Dict, List, Optional

from skills.base import Skill


class SkillRegistry:
    """技能注册表：单一发现点。注册的每个技能均可被 LLM 路由命中。"""

    def __init__(self):
        self._skills: Dict[str, Skill] = {}

    def register(self, skill: Skill) -> Skill:
        """注册技能。同 name 重复注册抛 ValueError（防误覆盖）。"""
        name = skill.metadata["name"]
        if not name:
            raise ValueError("技能 metadata['name'] 不能为空")
        if name in self._skills:
            raise ValueError(f"技能重名注册被拒绝: {name}")
        self._skills[name] = skill
        return skill

    def get(self, name: str) -> Optional[Skill]:
        """按 name 取技能。未注册返回 None。"""
        return self._skills.get(name)

    def has(self, name: str) -> bool:
        return name in self._skills

    def all_names(self) -> List[str]:
        return sorted(self._skills.keys())

    def list_all(self) -> List[Dict[str, str]]:
        """第一级清单（description 层）：每技能一行紧凑描述，供 LLM 判断该不该深入。"""
        return [
            {
                "name": s.metadata["name"],
                "version": s.metadata["version"],
                "summary": s.metadata["summary"],
                "triggers_hint": (
                    "；".join(s.metadata.get("triggers", [])[:2])
                    or s.metadata["summary"]
                ),
            }
            for s in sorted(self._skills.values(), key=lambda s: s.metadata["name"])
        ]

    def count(self) -> int:
        return len(self._skills)


# 默认全局注册表（供模块级 import 即用）
_default_registry = SkillRegistry()


def get_registry() -> SkillRegistry:
    return _default_registry