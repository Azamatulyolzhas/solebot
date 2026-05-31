import httpx

from config import WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_TOKEN

WHATSAPP_NUMBER_ID = WHATSAPP_PHONE_NUMBER_ID


async def send_whatsapp(to: str, text: str) -> None:
    """Отправить сообщение через WhatsApp Business API."""
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://graph.facebook.com/v18.0/{WHATSAPP_NUMBER_ID}/messages",
            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": text},
            },
        )
