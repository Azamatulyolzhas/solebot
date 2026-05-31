from admin_service import (
    count_logged_tokens,
    count_shop_messages,
    count_shop_rows,
    html_escape,
    list_recent_messages,
)
from products import list_products
from shops import resolve_shop_id


def render_admin_page(token: str, shop_id: int | None = None) -> str:
    shop_id = resolve_shop_id(shop_id)
    stats = {
        "Products": count_shop_rows("sneakers", shop_id),
        "Orders": count_shop_rows("orders", shop_id),
        "Conversations": count_shop_rows("conversations", shop_id),
        "Messages": count_shop_messages(shop_id),
        "Events": count_shop_rows("analytics_events", shop_id),
        "Tokens": count_logged_tokens(shop_id),
    }
    products = list_products(limit=80, shop_id=shop_id)
    messages = list_recent_messages(limit=20, shop_id=shop_id)

    stat_cards = "".join(
        f"<section class='metric'><span>{label}</span><strong>{value}</strong></section>"
        for label, value in stats.items()
    )
    product_rows = "".join(
        "<tr>"
        f"<td>{html_escape(p['brand'])}</td>"
        f"<td>{html_escape(p['model'])}</td>"
        f"<td>{html_escape(p.get('colorway'))}</td>"
        f"<td>{html_escape(p['size'])}</td>"
        f"<td>{html_escape(p['quantity'])}</td>"
        f"<td>{html_escape(p['price'])} в‚ё</td>"
        f"<td>{html_escape(p.get('category'))}</td>"
        f"<td>{html_escape(p.get('gender'))}</td>"
        "</tr>"
        for p in products
    )
    message_items = "".join(
        "<li>"
        f"<div><strong>{html_escape(m['channel'])}</strong> "
        f"<span>{html_escape(m['external_user_id'])}</span> "
        f"<em>{html_escape(m['role'])}</em></div>"
        f"<p>{html_escape(m['content'])}</p>"
        "</li>"
        for m in messages
    )

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SoleBot Admin</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #687385;
      --line: #dfe4ea;
      --accent: #0f766e;
      --accent-dark: #115e59;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }}
    header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      padding: 24px clamp(16px, 4vw, 40px);
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }}
    h1 {{ margin: 0; font-size: 24px; letter-spacing: 0; }}
    main {{ padding: 24px clamp(16px, 4vw, 40px); display: grid; gap: 24px; }}
    .metrics {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
    .metric, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .metric {{ padding: 16px; }}
    .metric span {{ display: block; color: var(--muted); font-size: 13px; }}
    .metric strong {{ display: block; margin-top: 8px; font-size: 28px; }}
    .panel {{ overflow: hidden; }}
    .panel-head {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      padding: 16px;
      border-bottom: 1px solid var(--line);
    }}
    h2 {{ margin: 0; font-size: 18px; letter-spacing: 0; }}
    form {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
    .actions {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
    input[type=file] {{ max-width: 260px; }}
    button, .button {{
      border: 0;
      border-radius: 6px;
      background: var(--accent);
      color: #fff;
      padding: 9px 12px;
      font-weight: 650;
      cursor: pointer;
      text-decoration: none;
      font-size: 14px;
    }}
    button:hover, .button:hover {{ background: var(--accent-dark); }}
    .table-wrap {{ overflow: auto; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 760px; }}
    th, td {{ padding: 11px 14px; border-bottom: 1px solid var(--line); text-align: left; font-size: 14px; }}
    th {{ color: var(--muted); font-size: 12px; text-transform: uppercase; background: #fbfcfd; }}
    ul {{ list-style: none; padding: 0; margin: 0; }}
    li {{ padding: 14px 16px; border-bottom: 1px solid var(--line); }}
    li div {{ display: flex; gap: 10px; flex-wrap: wrap; color: var(--muted); font-size: 13px; }}
    li p {{ margin: 8px 0 0; line-height: 1.45; }}
    @media (max-width: 760px) {{
      header {{ align-items: flex-start; flex-direction: column; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .panel-head {{ align-items: flex-start; flex-direction: column; }}
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>SoleBot Admin</h1>
    </div>
    <div class="actions">
      <a class="button" href="/admin/import-template?token={html_escape(token)}">CSV template</a>
      <a class="button" href="/admin/export?token={html_escape(token)}">Export CSV</a>
    </div>
  </header>
  <main>
    <section class="metrics">{stat_cards}</section>

    <section class="panel">
      <div class="panel-head">
        <h2>Catalog Import</h2>
        <form action="/admin/import-preview?token={html_escape(token)}" method="post" enctype="multipart/form-data">
          <input type="file" name="file" accept=".csv" required>
          <button type="submit">Preview CSV</button>
        </form>
        <form action="/admin/import?token={html_escape(token)}" method="post" enctype="multipart/form-data">
          <input type="file" name="file" accept=".csv" required>
          <button type="submit">Update CSV</button>
        </form>
        <form action="/admin/import?token={html_escape(token)}&replace=true" method="post" enctype="multipart/form-data">
          <input type="file" name="file" accept=".csv" required>
          <button type="submit">Replace catalog</button>
        </form>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2>Products</h2>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Brand</th><th>Model</th><th>Color</th><th>Size</th>
              <th>Qty</th><th>Price</th><th>Category</th><th>Gender</th>
            </tr>
          </thead>
          <tbody>{product_rows}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2>Recent Messages</h2>
      </div>
      <ul>{message_items}</ul>
    </section>
  </main>
</body>
</html>"""
