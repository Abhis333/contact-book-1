import csv
import io
import os
import random
import smtplib
import ssl
import string
from datetime import datetime, timedelta
from email.message import EmailMessage
from functools import wraps

from bson.objectid import ObjectId
from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_pymongo import PyMongo
from werkzeug.security import check_password_hash, generate_password_hash


def _load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-secret")
app.config["MONGO_URI"] = os.environ.get(
    "MONGO_URI",
    "mongodb://localhost:27017/contact_book",
)

MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "True").lower() in ["true", "1", "yes"]
MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", MAIL_USERNAME)

mongo = PyMongo(app)


def _log_activity(contact_id: str, action: str, details: str = ""):
    mongo.db.activities.insert_one({
        "contact_id": ObjectId(contact_id) if contact_id else None,
        "action": action,
        "details": details,
        "timestamp": datetime.utcnow(),
    })


def _get_contact_or_404(contact_id: str):
    try:
        contact = mongo.db.contacts.find_one({"_id": ObjectId(contact_id)})
    except Exception:
        contact = None

    if contact is None:
        abort(404)

    return contact


def _search_contacts(query: str):
    if not query:
        return []

    regex = {"$regex": query, "$options": "i"}
    return list(mongo.db.contacts.find({
        "$or": [
            {"name": regex},
            {"emails": regex},
            {"phones": regex},
            {"company": regex},
            {"tags": regex},
            {"notes": regex},
        ]
    }).sort("name", 1))


def _generate_otp() -> str:
    return "".join(random.choices(string.digits, k=6))


def _send_email(to_email: str, subject: str, content: str):
    if not MAIL_USERNAME or not MAIL_PASSWORD:
        raise RuntimeError("Email credentials are not configured.")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = MAIL_DEFAULT_SENDER or MAIL_USERNAME
    message["To"] = to_email
    message.set_content(content)

    context = ssl.create_default_context()
    with smtplib.SMTP(MAIL_SERVER, MAIL_PORT) as server:
        if MAIL_USE_TLS:
            server.starttls(context=context)
        server.login(MAIL_USERNAME, MAIL_PASSWORD)
        server.send_message(message)


def _create_otp(email: str, purpose: str) -> str:
    code = _generate_otp()
    now = datetime.utcnow()
    expires_at = now + timedelta(minutes=10)
    mongo.db.otps.insert_one({
        "email": email,
        "code": code,
        "purpose": purpose,
        "created_at": now,
        "expires_at": expires_at,
        "verified": False,
    })
    return code


def _verify_otp(email: str, code: str, purpose: str) -> bool:
    otp_record = mongo.db.otps.find_one({
        "email": email,
        "code": code,
        "purpose": purpose,
        "verified": False,
    })

    if not otp_record:
        return False

    if otp_record.get("expires_at") and otp_record["expires_at"] < datetime.utcnow():
        return False

    mongo.db.otps.update_one(
        {"_id": otp_record["_id"]},
        {"$set": {"verified": True}},
    )
    return True


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_email"):
            flash("Please log in first.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


@app.context_processor
def inject_current_user():
    user_email = session.get("user_email")
    if user_email:
        user = mongo.db.users.find_one({"email": user_email})
        return {"current_user": user}
    return {"current_user": None}


@app.route("/register", methods=["GET", "POST"])
def register():
    if session.get("user_email"):
        return redirect(url_for("index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if not email or not password or not confirm:
            flash("Email and password are required.", "error")
        elif password != confirm:
            flash("Password confirmation does not match.", "error")
        else:
            existing = mongo.db.users.find_one({"email": email})
            if existing and existing.get("is_verified"):
                flash("This email is already registered. Please log in.", "error")
            else:
                password_hash = generate_password_hash(password)
                mongo.db.users.update_one(
                    {"email": email},
                    {
                        "$set": {
                            "email": email,
                            "password_hash": password_hash,
                            "is_verified": False,
                            "created_at": datetime.utcnow(),
                        }
                    },
                    upsert=True,
                )
                otp_code = _create_otp(email, "register")
                try:
                    _send_email(
                        email,
                        "Your ContactBook registration OTP",
                        f"Your OTP code is: {otp_code}\n\nUse this code to verify your registration.",
                    )
                    flash("Verification OTP sent to your email.", "success")
                    return redirect(url_for("verify_register", email=email))
                except Exception as error:
                    flash(f"Unable to send OTP email: {error}", "error")

    return render_template("register.html")


@app.route("/verify-register", methods=["GET", "POST"])
def verify_register():
    email = request.args.get("email", "").strip().lower()
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        code = request.form.get("otp", "").strip()
        if _verify_otp(email, code, "register"):
            mongo.db.users.update_one(
                {"email": email},
                {"$set": {"is_verified": True}},
            )
            session["user_email"] = email
            flash("Registration confirmed. You are now logged in.", "success")
            return redirect(url_for("index"))
        flash("Invalid or expired OTP. Please try again.", "error")
    return render_template("verify.html", action="Register", email=email)


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_email"):
        return redirect(url_for("index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = mongo.db.users.find_one({"email": email})

        if not user or not check_password_hash(user.get("password_hash", ""), password):
            flash("Invalid email or password.", "error")
        elif not user.get("is_verified"):
            flash("Please verify your email before logging in.", "error")
            return redirect(url_for("verify_register", email=email))
        else:
            otp_code = _create_otp(email, "login")
            try:
                _send_email(
                    email,
                    "Your ContactBook login OTP",
                    f"Your OTP code is: {otp_code}\n\nUse this code to complete login.",
                )
                flash("Login OTP sent to your email.", "success")
                return redirect(url_for("verify_login", email=email))
            except Exception as error:
                flash(f"Unable to send OTP email: {error}", "error")

    return render_template("login.html")


@app.route("/verify-login", methods=["GET", "POST"])
def verify_login():
    email = request.args.get("email", "").strip().lower()
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        code = request.form.get("otp", "").strip()
        if _verify_otp(email, code, "login"):
            session["user_email"] = email
            flash("Login successful.", "success")
            return redirect(url_for("index"))
        flash("Invalid or expired OTP. Please try again.", "error")
    return render_template("verify.html", action="Login", email=email)


@app.route("/logout")
def logout():
    session.pop("user_email", None)
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    category = request.args.get("category", "")
    tag = request.args.get("tag", "")
    search_q = request.args.get("q", "").strip()

    if search_q:
        contacts = _search_contacts(search_q)
    else:
        query = {}
        if category:
            query["category"] = category
        if tag:
            query["tags"] = tag
        contacts = list(mongo.db.contacts.find(query).sort("name", 1))

    all_contacts = list(mongo.db.contacts.find())
    categories = sorted(set(c.get("category", "Personal") for c in all_contacts))
    all_tags = sorted(set(tag for c in all_contacts for tag in c.get("tags", [])))

    return render_template(
        "index.html",
        contacts=contacts,
        categories=categories,
        all_tags=all_tags,
        current_category=category,
        current_tag=tag,
        search_query=search_q,
    )


@app.route("/contact/<contact_id>")
@login_required
def view_contact(contact_id: str):
    contact = _get_contact_or_404(contact_id)
    activities = list(
        mongo.db.activities.find({"contact_id": ObjectId(contact_id)})
        .sort("timestamp", -1)
        .limit(20)
    )
    return render_template("view.html", contact=contact, activities=activities)


@app.route("/contact/new", methods=["GET", "POST"])
@login_required
def add_contact():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        emails = [e.strip() for e in request.form.get("emails", "").split(",") if e.strip()]
        phones = [p.strip() for p in request.form.get("phones", "").split(",") if p.strip()]
        category = request.form.get("category", "Personal")
        tags = [t.strip() for t in request.form.get("tags", "").split(",") if t.strip()]
        company = request.form.get("company", "").strip()
        address = request.form.get("address", "").strip()
        website = request.form.get("website", "").strip()
        notes = request.form.get("notes", "").strip()

        if not name:
            flash("Name is required.", "error")
        else:
            contact_doc = {
                "name": name,
                "emails": emails,
                "phones": phones,
                "category": category,
                "tags": tags,
                "company": company,
                "address": address,
                "website": website,
                "notes": notes,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
            result = mongo.db.contacts.insert_one(contact_doc)
            _log_activity(str(result.inserted_id), "created", f"Contact '{name}' created")
            flash("Contact added successfully.", "success")
            return redirect(url_for("index"))

    return render_template("form.html", contact=None, action="Add Contact")


@app.route("/contact/<contact_id>/edit", methods=["GET", "POST"])
@login_required
def edit_contact(contact_id: str):
    contact = _get_contact_or_404(contact_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        emails = [e.strip() for e in request.form.get("emails", "").split(",") if e.strip()]
        phones = [p.strip() for p in request.form.get("phones", "").split(",") if p.strip()]
        category = request.form.get("category", "Personal")
        tags = [t.strip() for t in request.form.get("tags", "").split(",") if t.strip()]
        company = request.form.get("company", "").strip()
        address = request.form.get("address", "").strip()
        website = request.form.get("website", "").strip()
        notes = request.form.get("notes", "").strip()

        if not name:
            flash("Name is required.", "error")
        else:
            mongo.db.contacts.update_one(
                {"_id": ObjectId(contact_id)},
                {
                    "$set": {
                        "name": name,
                        "emails": emails,
                        "phones": phones,
                        "category": category,
                        "tags": tags,
                        "company": company,
                        "address": address,
                        "website": website,
                        "notes": notes,
                        "updated_at": datetime.utcnow(),
                    }
                },
            )
            _log_activity(contact_id, "updated", f"Contact '{name}' updated")
            flash("Contact updated successfully.", "success")
            return redirect(url_for("view_contact", contact_id=contact_id))

    return render_template("form.html", contact=contact, action="Edit Contact")


@app.route("/contact/<contact_id>/delete", methods=["POST"])
@login_required
def delete_contact(contact_id: str):
    contact = _get_contact_or_404(contact_id)
    name = contact.get("name", "Unknown")
    mongo.db.contacts.delete_one({"_id": ObjectId(contact_id)})
    _log_activity(contact_id, "deleted", f"Contact '{name}' deleted")
    flash("Contact deleted successfully.", "success")
    return redirect(url_for("index"))


@app.route("/import", methods=["GET", "POST"])
@login_required
def import_contacts():
    if request.method == "POST":
        if "file" not in request.files:
            flash("No file part.", "error")
        else:
            file = request.files["file"]
            if file.filename == "":
                flash("No selected file.", "error")
            elif not file.filename.endswith(".csv"):
                flash("Only CSV files are supported.", "error")
            else:
                try:
                    stream = io.TextIOWrapper(file.stream, encoding="utf-8")
                    reader = csv.DictReader(stream)
                    count = 0
                    for row in reader:
                        if row.get("name", "").strip():
                            contact_doc = {
                                "name": row.get("name", "").strip(),
                                "emails": [
                                    e.strip()
                                    for e in row.get("emails", "").split(",")
                                    if e.strip()
                                ],
                                "phones": [
                                    p.strip()
                                    for p in row.get("phones", "").split(",")
                                    if p.strip()
                                ],
                                "category": row.get("category", "Personal").strip(),
                                "tags": [
                                    t.strip()
                                    for t in row.get("tags", "").split(",")
                                    if t.strip()
                                ],
                                "company": row.get("company", "").strip(),
                                "address": row.get("address", "").strip(),
                                "website": row.get("website", "").strip(),
                                "notes": row.get("notes", "").strip(),
                                "created_at": datetime.utcnow(),
                                "updated_at": datetime.utcnow(),
                            }
                            mongo.db.contacts.insert_one(contact_doc)
                            count += 1
                    _log_activity("", "bulk_import", f"Imported {count} contacts from CSV")
                    flash(f"Successfully imported {count} contacts.", "success")
                    return redirect(url_for("index"))
                except Exception as error:
                    flash(f"Error importing file: {error}", "error")

    return render_template("import.html")


@app.route("/export")
@login_required
def export_contacts():
    contacts = list(mongo.db.contacts.find().sort("name", 1))

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "name",
            "emails",
            "phones",
            "category",
            "tags",
            "company",
            "address",
            "website",
            "notes",
        ],
    )
    writer.writeheader()

    for contact in contacts:
        writer.writerow(
            {
                "name": contact.get("name", ""),
                "emails": ",".join(contact.get("emails", [])),
                "phones": ",".join(contact.get("phones", [])),
                "category": contact.get("category", ""),
                "tags": ",".join(contact.get("tags", [])),
                "company": contact.get("company", ""),
                "address": contact.get("address", ""),
                "website": contact.get("website", ""),
                "notes": contact.get("notes", ""),
            }
        )

    output.seek(0)
    _log_activity("", "export", f"Exported {len(contacts)} contacts to CSV")

    return send_file(
        io.BytesIO(output.getvalue().encode()),
        mimetype="text/csv",
        as_attachment=True,
        download_name="contacts.csv",
    )


@app.route("/api/tags")
def api_tags():
    all_contacts = list(mongo.db.contacts.find())
    tags = sorted(set(tag for c in all_contacts for tag in c.get("tags", [])))
    return jsonify(tags)


@app.route("/api/categories")
def api_categories():
    return jsonify(["Personal", "Work", "Family", "Friend", "Colleague", "Other"])


@app.route("/stats")
@login_required
def stats():
    total = mongo.db.contacts.count_documents({})
    by_category = {
        cat: mongo.db.contacts.count_documents({"category": cat})
        for cat in ["Personal", "Work", "Family", "Friend", "Colleague", "Other"]
    }
    all_contacts = list(mongo.db.contacts.find())
    all_tags = sorted(set(tag for c in all_contacts for tag in c.get("tags", [])))
    by_tag = {tag: mongo.db.contacts.count_documents({"tags": tag}) for tag in all_tags}

    return render_template("stats.html", total=total, by_category=by_category, by_tag=by_tag)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
