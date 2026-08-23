"""Administrative dashboard and management actions."""

from __future__ import annotations

import click
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .auth import admin_required
from .db import get_db
from .providers import encrypt_api_key

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.get("/")
@admin_required
def dashboard():
    database = get_db()
    stats = {
        "users": database.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "active_users": database.execute(
            "SELECT COUNT(*) FROM users WHERE is_active = 1"
        ).fetchone()[0],
        "jobs": database.execute("SELECT COUNT(*) FROM processing_jobs").fetchone()[0],
        "completed_jobs": database.execute(
            "SELECT COUNT(*) FROM processing_jobs WHERE status = 'completed'"
        ).fetchone()[0],
        "failed_jobs": database.execute(
            "SELECT COUNT(*) FROM processing_jobs WHERE status = 'failed'"
        ).fetchone()[0],
    }
    recent_jobs = database.execute(
        """SELECT j.id, j.original_filename, j.status, j.progress, j.created_at,
                  u.username
           FROM processing_jobs j JOIN users u ON u.id = j.user_id
           ORDER BY j.created_at DESC LIMIT 10"""
    ).fetchall()
    return render_template("admin/dashboard.html", stats=stats, recent_jobs=recent_jobs)


@admin_bp.get("/users")
@admin_required
def users():
    rows = (
        get_db()
        .execute(
            """SELECT u.*, COUNT(j.id) AS job_count
           FROM users u LEFT JOIN processing_jobs j ON j.user_id = u.id
           GROUP BY u.id ORDER BY u.created_at DESC"""
        )
        .fetchall()
    )
    return render_template("admin/users.html", users=rows)


@admin_bp.post("/users/<int:user_id>/toggle-active")
@admin_required
def toggle_user_active(user_id: int):
    if user_id == g.user["id"]:
        flash("لا يمكنك إيقاف حسابك الإداري الحالي.", "error")
        return redirect(url_for("admin.users"))
    database = get_db()
    user = database.execute("SELECT is_active FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        abort(404)
    database.execute(
        "UPDATE users SET is_active = ? WHERE id = ?", (int(not user["is_active"]), user_id)
    )
    database.commit()
    flash("تم تحديث حالة الحساب.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.post("/users/<int:user_id>/toggle-admin")
@admin_required
def toggle_user_admin(user_id: int):
    if user_id == g.user["id"]:
        flash("لا يمكنك إزالة صلاحية الإدارة من حسابك الحالي.", "error")
        return redirect(url_for("admin.users"))
    database = get_db()
    user = database.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        abort(404)
    database.execute(
        "UPDATE users SET is_admin = ? WHERE id = ?", (int(not user["is_admin"]), user_id)
    )
    database.commit()
    flash("تم تحديث صلاحية الإدارة.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.get("/jobs")
@admin_required
def jobs():
    rows = (
        get_db()
        .execute(
            """SELECT j.*, u.username FROM processing_jobs j
           JOIN users u ON u.id = j.user_id ORDER BY j.created_at DESC LIMIT 200"""
        )
        .fetchall()
    )
    return render_template("admin/jobs.html", jobs=rows)


@admin_bp.get("/providers")
@admin_required
def providers():
    rows = get_db().execute("SELECT * FROM ai_providers ORDER BY id DESC").fetchall()
    return render_template("admin/providers.html", providers=rows)


@admin_bp.post("/providers")
@admin_required
def create_provider():
    name = request.form.get("name", "").strip()
    provider_type = request.form.get("provider_type", "").strip().lower()
    model = request.form.get("model", "").strip()
    api_key = request.form.get("api_key", "").strip()
    if not name or provider_type not in {"gemini", "openai"} or not model or not api_key:
        flash("أكمل بيانات مزوّد الذكاء الاصطناعي بشكل صحيح.", "error")
        return redirect(url_for("admin.providers"))
    database = get_db()
    database.execute(
        """INSERT INTO ai_providers
           (name, provider_type, model, api_key_encrypted)
           VALUES (?, ?, ?, ?)""",
        (name[:80], provider_type, model[:120], encrypt_api_key(api_key)),
    )
    database.commit()
    flash("تمت إضافة المزوّد وحفظ المفتاح مشفّرًا.", "success")
    return redirect(url_for("admin.providers"))


@admin_bp.post("/providers/<int:provider_id>/activate")
@admin_required
def activate_provider(provider_id: int):
    database = get_db()
    provider = database.execute(
        "SELECT id FROM ai_providers WHERE id = ?", (provider_id,)
    ).fetchone()
    if provider is None:
        abort(404)
    database.execute("UPDATE ai_providers SET is_active = 0")
    database.execute(
        "UPDATE ai_providers SET is_active = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (provider_id,),
    )
    database.commit()
    flash("تم تفعيل المزوّد وسيُستخدم في الطلبات الجديدة.", "success")
    return redirect(url_for("admin.providers"))


@admin_bp.post("/providers/<int:provider_id>/delete")
@admin_required
def delete_provider(provider_id: int):
    database = get_db()
    provider = database.execute(
        "SELECT is_active FROM ai_providers WHERE id = ?", (provider_id,)
    ).fetchone()
    if provider is None:
        abort(404)
    if provider["is_active"]:
        flash("فعّل مزوّدًا آخر قبل حذف المزوّد الحالي.", "error")
    else:
        database.execute("DELETE FROM ai_providers WHERE id = ?", (provider_id,))
        database.commit()
        flash("تم حذف المزوّد.", "success")
    return redirect(url_for("admin.providers"))


@click.command("promote-admin")
@click.argument("email")
def promote_admin_command(email: str):
    database = get_db()
    cursor = database.execute(
        "UPDATE users SET is_admin = 1, is_active = 1 WHERE email = ?", (email.lower(),)
    )
    database.commit()
    if cursor.rowcount != 1:
        raise click.ClickException("No user found with that email")
    click.echo("Administrator access granted.")
