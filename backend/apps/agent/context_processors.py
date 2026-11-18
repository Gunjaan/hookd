from apps.agent.services import attention_count


def agent_nav(request) -> dict:
    """What waits for the seller (replies and DMs to review, Hooky's questions), shown next to "Replies" in the sidebar."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    return {"agent_pending_count": attention_count(user)}
