"""
Email notifications via Resend.
Set RESEND_API_KEY and EMAIL_FROM in .env to enable.
If RESEND_API_KEY is missing, emails are silently skipped (logged as warning).
"""
import logging

import resend

from config import EMAIL_FROM, RESEND_API_KEY, SHOP_DASHBOARD_URL

log = logging.getLogger(__name__)


def _send(to: str, subject: str, html: str) -> bool:
    if not RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set — email skipped (to=%s subject=%s)", to, subject)
        return False
    try:
        resend.api_key = RESEND_API_KEY
        resend.Emails.send({
            "from": EMAIL_FROM,
            "to": [to],
            "subject": subject,
            "html": html,
        })
        log.info("Email sent: to=%s subject=%s", to, subject)
        return True
    except Exception as e:
        log.error("Email send failed: to=%s subject=%s error=%s", to, subject, e)
        return False


def send_shop_approved(shop_name: str, owner_email: str) -> bool:
    dashboard_url = SHOP_DASHBOARD_URL.rstrip("/") + "/shop"
    html = f"""
    <div style="font-family: sans-serif; max-width: 520px; margin: 0 auto; padding: 32px;">
      <h2 style="color:#16a34a;">Ваш магазин одобрен! 🎉</h2>
      <p>Привет! Ваш магазин <strong>{shop_name}</strong> прошёл проверку и теперь активен.</p>
      <p>Войдите в личный кабинет, чтобы подключить Telegram-бота и загрузить каталог:</p>
      <a href="{dashboard_url}"
         style="display:inline-block;margin-top:16px;padding:12px 24px;
                background:#16a34a;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;">
        Открыть кабинет
      </a>
      <p style="margin-top:32px;color:#6b7280;font-size:13px;">
        Если вы не регистрировались — просто проигнорируйте это письмо.
      </p>
    </div>
    """
    return _send(owner_email, f"Ваш магазин «{shop_name}» одобрен", html)


def send_shop_rejected(shop_name: str, owner_email: str) -> bool:
    html = f"""
    <div style="font-family: sans-serif; max-width: 520px; margin: 0 auto; padding: 32px;">
      <h2 style="color:#dc2626;">Заявка отклонена</h2>
      <p>К сожалению, заявка на регистрацию магазина <strong>{shop_name}</strong> была отклонена.</p>
      <p>Если вы считаете это ошибкой — обратитесь в поддержку.</p>
    </div>
    """
    return _send(owner_email, f"Заявка на регистрацию «{shop_name}» отклонена", html)


def send_password_reset(owner_email: str, reset_token: str) -> bool:
    reset_url = SHOP_DASHBOARD_URL.rstrip("/") + f"/shop?token={reset_token}"
    html = f"""
    <div style="font-family: sans-serif; max-width: 520px; margin: 0 auto; padding: 32px;">
      <h2 style="color:#2563eb;">Сброс пароля</h2>
      <p>Вы запросили сброс пароля для вашего аккаунта SoleBot.</p>
      <p>Нажмите кнопку ниже, чтобы задать новый пароль. Ссылка действительна <strong>1 час</strong>.</p>
      <a href="{reset_url}"
         style="display:inline-block;margin-top:16px;padding:12px 24px;
                background:#2563eb;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;">
        Сбросить пароль
      </a>
      <p style="margin-top:24px;color:#6b7280;font-size:12px;">
        Если вы не запрашивали сброс пароля — просто проигнорируйте это письмо.
      </p>
    </div>
    """
    return _send(owner_email, "Сброс пароля SoleBot", html)


def send_shop_registered(shop_name: str, owner_email: str) -> bool:
    """Confirmation email right after shop submits registration."""
    html = f"""
    <div style="font-family: sans-serif; max-width: 520px; margin: 0 auto; padding: 32px;">
      <h2 style="color:#2563eb;">Заявка получена</h2>
      <p>Привет! Мы получили вашу заявку на регистрацию магазина <strong>{shop_name}</strong>.</p>
      <p>Обычно проверка занимает до 24 часов. После одобрения вы получите ещё одно письмо.</p>
      <p style="margin-top:32px;color:#6b7280;font-size:13px;">
        Если вы не регистрировались — просто проигнорируйте это письмо.
      </p>
    </div>
    """
    return _send(owner_email, f"Заявка на регистрацию «{shop_name}» получена", html)
