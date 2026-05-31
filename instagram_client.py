import httpx

from config import INSTAGRAM_TOKEN


async def send_instagram(recipient_id: str, text: str) -> None:
    """Отправить сообщение через Instagram Messenger API."""
    async with httpx.AsyncClient() as client:
        await client.post(
            "https://graph.facebook.com/v18.0/me/messages",
            params={"access_token": INSTAGRAM_TOKEN},
            json={
                "recipient": {"id": recipient_id},
                "message": {"text": text},
            },
        )
