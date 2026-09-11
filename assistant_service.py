"""Verified, database-backed management assistant for ADUFARMS."""
from __future__ import annotations

from datetime import date, timedelta


def _money(value):
    return f"GH₵{float(value or 0):,.2f}"


def answer_question(conn, question: str) -> dict:
    """Answer supported business questions from current database facts only."""
    text = (question or "").strip().lower()
    today = date.today()
    if not text:
        return {"title": "Ask ADUFARMS", "answer": "Enter a business question to search verified ADUFARMS records.", "facts": []}

    if "available" in text and ("stock" in text or "maize" in text):
        purchased = conn.execute("SELECT COALESCE(SUM(quantity_received_kg),0) FROM purchases WHERE deleted=0").fetchone()[0]
        sold = conn.execute("SELECT COALESCE(SUM(quantity_kg),0) FROM sales WHERE deleted=0").fetchone()[0]
        return {"title": "Current stock", "answer": f"Current available stock is {float(purchased) - float(sold):,.2f} KG.", "facts": [f"Purchased: {float(purchased):,.2f} KG", f"Sold: {float(sold):,.2f} KG"]}

    if "owe" in text or "outstanding" in text or "unpaid" in text:
        rows = conn.execute("""SELECT c.name, COALESCE(SUM(s.total_sale),0)+c.opening_balance billed,
            COALESCE((SELECT SUM(p.amount) FROM payments p JOIN sales ps ON ps.transaction_id=p.transaction_id
                      WHERE ps.customer_id=c.id AND p.deleted=0),0) paid
            FROM customers c LEFT JOIN sales s ON s.customer_id=c.id AND s.deleted=0
            WHERE c.active=1 GROUP BY c.id ORDER BY (billed-paid) DESC""").fetchall()
        balances = [(r["name"], max(float(r["billed"]) - float(r["paid"]), 0)) for r in rows]
        balances = [(name, value) for name, value in balances if value > 0.005]
        if not balances:
            return {"title": "Outstanding balances", "answer": "No outstanding customer balances were found.", "facts": []}
        return {"title": "Outstanding balances", "answer": f"{len(balances)} customer(s) have outstanding balances.", "facts": [f"{name}: {_money(value)}" for name, value in balances[:10]]}

    if "today" in text or "yesterday" in text or "month" in text:
        if "yesterday" in text:
            target = today - timedelta(days=1)
            start = end = target.isoformat()
            label = "yesterday"
        elif "month" in text:
            start = today.replace(day=1).isoformat()
            end = today.isoformat()
            label = "this month"
        else:
            start = end = today.isoformat()
            label = "today"
        sales = conn.execute("SELECT COALESCE(SUM(total_sale),0), COALESCE(SUM(quantity_kg),0), COUNT(*) FROM sales WHERE deleted=0 AND sale_date BETWEEN ? AND ?", (start, end)).fetchone()
        payments = conn.execute("SELECT COALESCE(SUM(amount),0), COUNT(*) FROM payments WHERE deleted=0 AND payment_date BETWEEN ? AND ?", (start, end)).fetchone()
        return {"title": f"Activity for {label}", "answer": f"Sales total {_money(sales[0])} across {sales[2]} sale(s); payments received {_money(payments[0])} across {payments[1]} payment(s).", "facts": [f"Maize sold: {float(sales[1]):,.2f} KG", f"Period: {start} to {end}"]}

    if "customer" in text and ("most" in text or "top" in text):
        rows = conn.execute("""SELECT c.name, COALESCE(SUM(s.total_sale),0) total FROM customers c
            JOIN sales s ON s.customer_id=c.id AND s.deleted=0 GROUP BY c.id ORDER BY total DESC LIMIT 5""").fetchall()
        return {"title": "Top customers", "answer": "Top customers by billed sales are listed below.", "facts": [f"{r['name']}: {_money(r['total'])}" for r in rows] or ["No sales data is available."]}

    return {"title": "Verified data assistant", "answer": "I could not match that to a supported database question. Try asking about stock, sales, payments, outstanding balances, top customers, or today's/month's activity.", "facts": []}
