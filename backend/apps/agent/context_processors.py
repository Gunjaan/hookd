from apps.agent.services import pending_review_count


def agent_nav(request) -> dict:
    """Number of replies waiting for review, shown next to "Agent" in the sidebar."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    return {"agent_pending_count": pending_review_count(user)}
