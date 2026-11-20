"""种子员工固定形象：名字 -> (头像预设, 形象配置)。

与开发库已回填值一致（2026-11-19 第二轮补配）。总管 ensure_team 与
workspace seed_demo 共用同一份映射：新库创建与旧行回填都走它，
避免两处各写一份漂移。形象配置契约见 docs/APPEARANCE.md；
头像预设见 apps/desktop/src/components/AgentAvatar.tsx。
"""
SEED_AGENT_FACES = {
    "产品经理": ("avatar-03", {"gender": "female", "skin": 1, "outfit": 2, "hair": 3}),
    "开发工程师": ("avatar-07", {"gender": "male", "skin": 4, "outfit": 5, "hair": 3}),
    "测试工程师": ("avatar-04", {"gender": "female", "skin": 3, "outfit": 6, "hair": 4}),
}


def backfill_seed_face(agent):
    """给缺头像/形象的种子员工补上固定配置；已有配置一律不动。

    新行也走这里：avatar 列默认 ""、appearance 列默认 {}，都是 falsy。
    返回是否改动（调用方据此决定是否提交）。
    """
    face = SEED_AGENT_FACES.get(agent.name)
    if face is None:
        return False
    changed = False
    if not getattr(agent, "avatar", None):
        agent.avatar = face[0]
        changed = True
    if not getattr(agent, "appearance", None):
        agent.appearance = dict(face[1])
        changed = True
    return changed
