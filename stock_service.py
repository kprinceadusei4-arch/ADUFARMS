"""Centralized stock logic for ADUFARMS. All stock-affecting ops must use this module.

Rules:
  PURCHASE RECEIVED -> increases stock (+qty)
  SALE -> decreases stock (-qty)
  SALE REVERSAL -> restores stock (+qty)
  PURCHASE REVERSAL -> removes received stock (-qty)
  SALE RESTORATION -> decreases stock again (-qty)
  PURCHASE RESTORATION -> increases stock again (+qty)

Never allow sale > available stock. Never allow negative stock.
All operations using a caller-supplied sqlite3 connection so they stay atomic.
"""
from __future__ import annotations


def available_stock(conn) -> float:
    purchased = conn.execute(
        "SELECT COALESCE(SUM(quantity_received_kg),0) v FROM purchases WHERE deleted=0"
    ).fetchone()["v"]
    sold = conn.execute(
        "SELECT COALESCE(SUM(quantity_kg),0) v FROM sales WHERE deleted=0"
    ).fetchone()["v"]
    return float(purchased) - float(sold)


def assert_stock_available(conn, qty: float) -> None:
    if qty <= 0:
        raise ValueError("Quantity must be greater than zero.")
    if qty > available_stock(conn) + 1e-9:
        raise ValueError(
            f"Insufficient stock. Available: {available_stock(conn):,.2f} KG."
        )


def record_movement(conn, movement_type: str, reference: str, quantity: float,
                    movement_date: str, created_by: str, notes: str = "",
                    created_at: str = "") -> None:
    if movement_type not in ("PURCHASE", "SALE", "REVERSAL", "ADJUSTMENT"):
        raise ValueError("Invalid movement type.")
    conn.execute(
        """INSERT INTO stock_movements
           (movement_type,reference,quantity_kg,movement_date,created_by,notes,created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (movement_type, reference, float(quantity), movement_date,
         created_by, notes, created_at),
    )


def apply_purchase_create(conn, reference: str, received: float, movement_date: str,
                          created_by: str, created_at: str) -> None:
    if received < 0:
        raise ValueError("Quantity received cannot be negative.")
    if received > 0:
        record_movement(conn, "PURCHASE", reference, float(received),
                        movement_date, created_by, "Purchase received", created_at)


def apply_sale_create(conn, reference: str, qty: float, movement_date: str,
                      created_by: str, created_at: str) -> None:
    assert_stock_available(conn, qty)
    record_movement(conn, "SALE", reference, -float(qty),
                    movement_date, created_by, "Sale", created_at)


def apply_purchase_edit(conn, reference: str, old_received: float, new_received: float,
                        movement_date: str, created_by: str, created_at: str) -> None:
    """Adjust stock by delta. Guards against negative stock."""
    delta = float(new_received) - float(old_received)
    if delta > 0:
        # increasing received stock is always safe
        record_movement(conn, "ADJUSTMENT", reference, delta,
                        movement_date, created_by,
                        f"Purchase edit {old_received:g}->{new_received:g} KG", created_at)
    elif delta < 0:
        # removing stock must not drive total negative
        if available_stock(conn) + delta < -1e-9:
            raise ValueError(
                "This purchase edit cannot be saved because it would make stock negative."
            )
        record_movement(conn, "ADJUSTMENT", reference, delta,
                        movement_date, created_by,
                        f"Purchase edit {old_received:g}->{new_received:g} KG", created_at)


def apply_sale_edit(conn, reference: str, old_qty: float, new_qty: float,
                    movement_date: str, created_by: str, created_at: str) -> None:
    """Sale qty increase consumes stock; decrease releases stock."""
    delta = float(new_qty) - float(old_qty)  # >0 means more sold
    if delta > 0 and delta > available_stock(conn) + 1e-9:
        raise ValueError(
            f"Insufficient stock. Available: {available_stock(conn):,.2f} KG."
        )
    if delta != 0:
        record_movement(conn, "ADJUSTMENT", reference, -delta,
                        movement_date, created_by,
                        f"Sale edit {old_qty:g}->{new_qty:g} KG", created_at)


def apply_reversal(conn, table: str, reference: str, qty: float,
                   movement_date: str, created_by: str, created_at: str,
                   reason: str) -> None:
    if table == "sales":
        # restore stock
        record_movement(conn, "REVERSAL", reference, float(qty),
                        movement_date, created_by, reason, created_at)
    elif table == "purchases":
        # remove received stock; must not go negative
        if float(qty) > available_stock(conn) + 1e-9:
            raise ValueError(
                "This purchase cannot be reversed because it would make stock negative."
            )
        record_movement(conn, "REVERSAL", reference, -float(qty),
                        movement_date, created_by, reason, created_at)
    elif table == "payments":
        record_movement(conn, "REVERSAL", reference, 0,
                        movement_date, created_by, reason, created_at)


def apply_restoration(conn, table: str, reference: str, qty: float,
                      movement_date: str, created_by: str, created_at: str) -> None:
    if table == "sales":
        if float(qty) > available_stock(conn) + 1e-9:
            raise ValueError(
                "This sale cannot be restored because available stock is insufficient."
            )
        record_movement(conn, "REVERSAL", reference, -float(qty),
                        movement_date, created_by, "Restored transaction", created_at)
    elif table == "purchases":
        record_movement(conn, "REVERSAL", reference, float(qty),
                        movement_date, created_by, "Restored transaction", created_at)
    elif table == "payments":
        record_movement(conn, "REVERSAL", reference, 0,
                        movement_date, created_by, "Restored transaction", created_at)