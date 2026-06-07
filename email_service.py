"""
Email notifications via Resend.
Set RESEND_API_KEY and EMAIL_FROM in .env to enable.
If RESEND_API_KEY is missing, emails are silently skipped (logged as warning).
"""
import logging

from config import RESEND_API_KEY, SHOP_DASHBOARD_URL
from resend_email import get_email_status, send_email

log = logging.getLogger(__name__)


def _fmt_date(value) -> str:
    if not value:
        return "—"
    text = str(value)
    return text[:10] if len(text) >= 10 else text


def _fmt_messages_limit(limit) -> str:
    try:
        n = int(limit or 0)
    except (TypeError, ValueError):
        return "—"
    return "безлимит" if n >= 999_999 else str(n)


def _subscription_rows(sub: dict | None) -> str:
    if not sub:
        return ""
    plan = (sub.get("plan") or "trial").upper()
    limit = _fmt_messages_limit(sub.get("messages_limit"))
    ends = _fmt_date(sub.get("period_ends_at") or sub.get("trial_ends_at"))
    status = sub.get("status") or "active"
    return f"""
      <tr><td style="padding:6px 0;color:#6b7280;">Тариф</td><td><strong>{plan}</strong></td></tr>
      <tr><td style="padding:6px 0;color:#6b7280;">Статус</td><td>{status}</td></tr>
      <tr><td style="padding:6px 0;color:#6b7280;">Лимит сообщений</td><td>{limit}</td></tr>
      <tr><td style="padding:6px 0;color:#6b7280;">Действует до</td><td>{ends}</td></tr>
    """


def email_delivery_status() -> dict:
    return get_email_status()


def _send(to: str, subject: str, html: str) -> bool:
    if not RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set — email skipped (to=%s subject=%s)", to, subject)
        return False
    ok, err = send_email(to, subject, html)
    if not ok:
        status = get_email_status()
        if status.get("sandbox_mode"):
            log.warning(
                "Email to %s failed (%s). Sandbox mode — verify a domain in /admin → Email.",
                to,
                err,
            )
        else:
            log.error("Email send failed to=%s: %s", to, err)
    return ok


def send_shop_approved(shop_name: str, owner_email: str, subscription: dict | None = None) -> bool:
    dashboard_url = SHOP_DASHBOARD_URL.rstrip("/") + "/shop"
    sub_block = ""
    if subscription:
        sub_block = f"""
      <h3 style="margin-top:24px;font-size:1rem;">Ваша подписка</h3>
      <table style="width:100%;border-collapse:collapse;font-size:15px;">
        {_subscription_rows(subscription)}
      </table>
        """
    html = f"""
    <div style="font-family: sans-serif; max-width: 520px; margin: 0 auto; padding: 32px;">
      <h2 style="color:#16a34a;">Ваш магазин одобрен! 🎉</h2>
      <p>Привет! Ваш магазин <strong>{shop_name}</strong> прошёл проверку и теперь активен.</p>
      {sub_block}
      <p>Войдите в личный кабинет, чтобы подключить Telegram-бота и загрузить каталог:</p>
      <a href="{dashboard_url}"
         style="display:inline-block;margin-top:16px;padding:12px 24px;
                background:#16a34a;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;">
        Открыть кабинет
      </a>
      <p style="margin-top:32px;color:#6b7280;font-size:13px;">
        Письмо отправлено на email, указанный при регистрации.
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
      <p>Вы запросили сброс пароля для вашего аккаунта SaleBot.</p>
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
    return _send(owner_email, "Сброс пароля SaleBot", html)


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


def send_new_order(
    shop_name: str,
    owner_email: str,
    order_id: int | None,
    state: dict,
    channel: str,
    external_user_id: str,
) -> bool:
    dashboard_url = SHOP_DASHBOARD_URL.rstrip("/") + "/shop"
    product = (state.get("product_interest") or "").strip() or "—"
    html = f"""
    <div style="font-family: sans-serif; max-width: 520px; margin: 0 auto; padding: 32px;">
      <h2 style="color:#2563eb;">Новый заказ — {shop_name}</h2>
      <table style="width:100%;border-collapse:collapse;font-size:15px;">
        <tr><td style="padding:6px 0;color:#6b7280;">ID</td><td><strong>{order_id or '—'}</strong></td></tr>
        <tr><td style="padding:6px 0;color:#6b7280;">Канал</td><td>{channel}</td></tr>
        <tr><td style="padding:6px 0;color:#6b7280;">Клиент</td><td>{external_user_id}</td></tr>
        <tr><td style="padding:6px 0;color:#6b7280;">Имя</td><td>{state.get('name', '')}</td></tr>
        <tr><td style="padding:6px 0;color:#6b7280;">Телефон</td><td>{state.get('phone', '')}</td></tr>
        <tr><td style="padding:6px 0;color:#6b7280;">Товар</td><td><strong>{product}</strong></td></tr>
      </table>
      <a href="{dashboard_url}"
         style="display:inline-block;margin-top:20px;padding:12px 24px;
                background:#2563eb;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;">
        Открыть заказы в кабинете
      </a>
      <p style="margin-top:24px;color:#6b7280;font-size:12px;">
        Уведомление о заказе для владельца магазина ({owner_email}).
      </p>
    </div>
    """
    return _send(owner_email, f"Новый заказ #{order_id or '—'} — {shop_name}", html)


def send_subscription_updated(
    shop_name: str,
    owner_email: str,
    subscription: dict | None,
    *,
    reason: str = "updated",
) -> bool:
    """Notify shop owner about subscription on registration email."""
    dashboard_url = SHOP_DASHBOARD_URL.rstrip("/") + "/shop"
    titles = {
        "updated": "Подписка обновлена",
        "activated": "Подписка активирована",
    }
    title = titles.get(reason, "Информация о подписке")
    html = f"""
    <div style="font-family: sans-serif; max-width: 520px; margin: 0 auto; padding: 32px;">
      <h2 style="color:#2563eb;">{title} — {shop_name}</h2>
      <p>Актуальные условия вашего тарифа:</p>
      <table style="width:100%;border-collapse:collapse;font-size:15px;">
        {_subscription_rows(subscription)}
      </table>
      <a href="{dashboard_url}"
         style="display:inline-block;margin-top:20px;padding:12px 24px;
                background:#2563eb;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;">
        Открыть кабинет
      </a>
      <p style="margin-top:24px;color:#6b7280;font-size:12px;">
        Письмо отправлено на email, указанный при регистрации магазина.
      </p>
    </div>
    """
    return _send(owner_email, f"{title} — {shop_name}", html)
