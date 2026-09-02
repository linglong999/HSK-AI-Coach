# ============================================================
# skills/__init__.py
# M1 Skill 化 · 五内核技能装配
# 提供 build_registry()：构造带全部内核技能的注册表（单一装配点）
# 引擎实例可注入（测试/无 Key 友好），默认懒加载真实引擎
# 0.18 接入项②：注册 retrieve_corpus（第 9 个）；RAG 索引在此构建一次、
# 两个检索技能（lookup_knowledge_point / retrieve_corpus）共享同一实例
# ============================================================

from skills.registry import SkillRegistry
from skills.identify_errors import IdentifyErrorsSkill
from skills.explain_error import ExplainErrorSkill
from skills.verify_retell import VerifyRetellSkill
from skills.lookup_knowledge_point import LookupKnowledgePointSkill
from skills.retrieve_corpus import RetrieveCorpusSkill
from skills.get_review_queue import GetReviewQueueSkill
from skills.web_search import WebSearchSkill
from skills.parse_document import ParseDocumentSkill
from skills.generate_unit import GenerateUnitSkill


def build_registry(**overrides) -> SkillRegistry:
    """构造已注册技能（五内核 + M7 检索 + M9 两工具 + 生成引擎 wrapper）的注册表。

    overrides 可注入引擎实例（如 recognizer=..., graph=..., generation=...），
    便于测试与无 Key 环境。
    rag 可注入现成索引实例（测试）；默认构建共享三源索引（KB+词表+图谱，
    实测约 11ms），lookup_knowledge_point 与 retrieve_corpus 复用同一实例。
    """
    graph = overrides.get("graph")
    rag = overrides.get("rag")
    if rag is None:
        try:
            from engine.rag import build_hsk_index
            # 共享索引含 graph 源；lookup 的 kp_only 查询不受 graph 源影响
            rag = build_hsk_index(graph=graph)
        except Exception:
            rag = None   # 数据缺失时两技能各自走降级路径（lookup 子串兜底 / retrieve 报错）

    reg = SkillRegistry()
    reg.register(IdentifyErrorsSkill(recognizer=overrides.get("recognizer"),
                                     graph=graph))
    reg.register(ExplainErrorSkill(explainer=overrides.get("explainer"),
                                   graph=graph))
    reg.register(VerifyRetellSkill(verifier=overrides.get("verifier"),
                                   graph=graph))
    reg.register(LookupKnowledgePointSkill(
        path=overrides.get("knowledge_points_path"), rag=rag))
    reg.register(RetrieveCorpusSkill(rag=rag, graph=graph))
    reg.register(GetReviewQueueSkill(graph=graph))
    reg.register(GenerateUnitSkill(engine=overrides.get("generation")))
    reg.register(WebSearchSkill())
    reg.register(ParseDocumentSkill())
    return reg


__all__ = [
    "build_registry",
    "SkillRegistry",
    "IdentifyErrorsSkill",
    "ExplainErrorSkill",
    "VerifyRetellSkill",
    "LookupKnowledgePointSkill",
    "RetrieveCorpusSkill",
    "GetReviewQueueSkill",
    "GenerateUnitSkill",
    "WebSearchSkill",
    "ParseDocumentSkill",
]