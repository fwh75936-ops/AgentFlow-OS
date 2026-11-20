from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.teams import chat

router = APIRouter(prefix="/api/teams", tags=["team-chat"])


@router.post("/{team_id}/channel/messages", status_code=201)
def post_channel_message(team_id: int, body: dict, db: Session = Depends(get_db)):
    sender = body.get("sender_agent_id")
    if sender is not None and not isinstance(sender, int):
        raise HTTPException(422, "sender_agent_id 必须是整数或不填")
    mentions = body.get("mentions")
    if mentions is not None and not isinstance(mentions, list):
        raise HTTPException(422, "mentions 必须是数组")
    if not isinstance(body.get("content", ""), str):
        raise HTTPException(422, "content 必须是字符串")
    try:
        return chat.post_message(db, team_id, body.get("content", ""),
                                 sender_agent_id=sender,
                                 mentions=mentions,
                                 mode=body.get("mode", "direct"),
                                 reply_to=body.get("reply_to"))
    except chat.ChatError as e:
        raise HTTPException(400, str(e))


@router.post("/{team_id}/channel/messages/{message_id}/pin")
def pin_channel_message(team_id: int, message_id: str, db: Session = Depends(get_db)):
    try:
        return chat.pin_message(db, team_id, message_id)
    except chat.ChatError as e:
        raise HTTPException(404, str(e))


@router.get("/{team_id}/channel/summary")
def get_channel_summary(team_id: int, db: Session = Depends(get_db)):
    try:
        return chat.channel_summary(db, team_id)
    except chat.ChatError as e:
        raise HTTPException(404, str(e))


@router.get("/{team_id}/channel/messages")
def channel_messages(team_id: int, limit: int = 100, include_directed: bool = False,
                     db: Session = Depends(get_db)):
    try:
        return chat.list_messages(db, team_id, limit=limit,
                                  include_directed=include_directed)
    except chat.ChatError as e:
        raise HTTPException(404, str(e))


@router.get("/{team_id}/channel/mentions/{agent_id}")
def channel_mentions(team_id: int, agent_id: int, db: Session = Depends(get_db)):
    try:
        return chat.list_mentions(db, team_id, agent_id)
    except chat.ChatError as e:
        raise HTTPException(404, str(e))
