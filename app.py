import os
import sqlite3
import mercadopago
import json
import traceback
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from datetime import datetime, timezone
import pytz
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'uma_chave_secreta_bem_aleatoria_para_testes_dhgsfdyg')

DATABASE = 'database.db'
MP_ACCESS_TOKEN = os.getenv('MP_ACCESS_TOKEN_PROD')
MP_PUBLIC_KEY = os.getenv('MP_PUBLIC_KEY_PROD')
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'senhaforte123')

if not MP_ACCESS_TOKEN or not MP_PUBLIC_KEY:
    raise ValueError("MP_ACCESS_TOKEN e MP_PUBLIC_KEY devem ser configurados nas variáveis de ambiente ou .env.")
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

def get_db():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS registrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                whatsapp TEXT NOT NULL,
                time_torce TEXT NOT NULL,
                email TEXT,
                payment_id TEXT,
                preference_id TEXT,
                payment_status TEXT DEFAULT 'pending_creation',
                link_sent INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                payment_approved_at DATETIME
            )
        ''')
        db.commit()
        print("Banco de dados inicializado ou já existente.")

def format_datetime_local(value, tz_name='America/Sao_Paulo', fmt='%d/%m/%Y %H:%M:%S'):
    if value == "now":
        now_utc = datetime.now(timezone.utc)
        try:
            local_tz = pytz.timezone(tz_name)
            local_now = now_utc.astimezone(local_tz)
            return local_now.strftime(fmt)
        except Exception as e: return now_utc.strftime(fmt) + " (UTC)"
    if not value: return ""
    utc_dt = None
    if isinstance(value, str):
        try:
            value_clean = value.split('.')[0]
            if len(value_clean) == 19: utc_dt = datetime.strptime(value_clean, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
            else: utc_dt = datetime.fromisoformat(value.replace('Z', '+00:00')).replace(tzinfo=timezone.utc)
        except ValueError:
            try: dt_naive = datetime.strptime(value_clean, '%Y-%m-%d %H:%M:%S'); utc_dt = dt_naive.replace(tzinfo=timezone.utc)
            except ValueError: return value
    elif isinstance(value, datetime):
        utc_dt = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    else: return value
    if utc_dt:
        try: local_tz = pytz.timezone(tz_name); local_dt = utc_dt.astimezone(local_tz); return local_dt.strftime(fmt)
        except pytz.exceptions.UnknownTimeZoneError: return utc_dt.strftime(fmt) + " (UTC-Fuso?)"
        except Exception: return utc_dt.strftime(fmt) + " (UTC-Conv?)"
    return value
app.jinja_env.filters['datetime_local'] = format_datetime_local

@app.route('/')
def index():
    return render_template('index.html', mp_public_key=MP_PUBLIC_KEY)

@app.route('/create_preference', methods=['POST'])
def create_preference():
    data = request.get_json()
    name = data.get('name')
    whatsapp = data.get('whatsapp')
    time_torce = data.get('time_torce')
    email_usuario = data.get('email')

    if not name or not whatsapp or not time_torce:
        missing = [f for f, v in [("Nome", name), ("WhatsApp", whatsapp), ("Time que torce", time_torce)] if not v]
        return jsonify({'error': f"Campos obrigatórios: {', '.join(missing)}."}), 400

    db = get_db()
    cursor = db.cursor()
    registration_id = None
    try:
        cursor.execute(
            "INSERT INTO registrations (name, whatsapp, time_torce, email, payment_status) VALUES (?, ?, ?, ?, ?)",
            (name, whatsapp, time_torce, email_usuario if email_usuario else None, 'pending_creation')
        )
        db.commit()
        registration_id = cursor.lastrowid
        print(f"Novo registro ID: {registration_id} para {name}, time: {time_torce}, email usuário: {email_usuario}")

        # <<< URL DO NGROK ATUALIZADA DIRETAMENTE NO CÓDIGO >>>
        NGROK_BASE_URL = "https://32ab-190-102-47-31.ngrok-free.app"
        # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<

        clean_whatsapp = "".join(filter(str.isdigit, whatsapp))
        phone_details = {}
        if len(clean_whatsapp) >= 10:
            phone_details["area_code"] = clean_whatsapp[:2]
            phone_details["number"] = clean_whatsapp[2:]
        
        payer_email_para_mp = email_usuario if email_usuario else f"user_{registration_id}@dkfute.com" # Use um domínio seu aqui se tiver

        preference_data = {
            "items": [{"title": "Acesso à Transmissão de Futebol", "quantity": 1, "unit_price": 3.00, "currency_id": "BRL"}],
            "payer": {
                "name": name,
                "phone": phone_details if phone_details else None,
                "email": payer_email_para_mp
            },
            "back_urls": {
                "success": f"{NGROK_BASE_URL}{url_for('payment_feedback')}",
                "failure": f"{NGROK_BASE_URL}{url_for('payment_feedback')}",
                "pending": f"{NGROK_BASE_URL}{url_for('payment_feedback')}"
            },
            "notification_url": f"{NGROK_BASE_URL}{url_for('webhook_mercadopago')}",
            "external_reference": str(registration_id)
        }
        if not preference_data["payer"]["phone"]: del preference_data["payer"]["phone"]
        
        print(f"--- DADOS ENVIADOS PARA CRIAR PREFERÊNCIA (NGROK: {NGROK_BASE_URL}) ---")
        print(json.dumps(preference_data, indent=2, ensure_ascii=False))

        preference_response = sdk.preference().create(preference_data)
        
        print("--- RESPOSTA DA API DO MERCADO PAGO (Criação Preferência) ---")
        print(json.dumps(preference_response, indent=2, ensure_ascii=False))
        if not preference_response or preference_response.get("status") not in [200, 201]:
            error_details = preference_response.get("response", {}) if isinstance(preference_response, dict) else {}
            error_message = error_details.get("message", "Erro desconhecido MP.")
            if isinstance(error_details.get("cause"), list) and error_details.get("cause"):
                 error_message += " Causa: " + str(error_details.get("cause")[0].get("description",""))
            raise Exception(f"Erro API MP: {error_message}")
        preference_details = preference_response.get("response")
        preference_id_mp = preference_details.get('id')
        init_point_url = preference_details.get('init_point')
        if not preference_id_mp or not init_point_url:
            raise Exception("ID ou init_point não encontrados na resposta MP.")
        cursor.execute("UPDATE registrations SET preference_id = ?, payment_status = ? WHERE id = ?",
                       (preference_id_mp, 'pending_payment', registration_id)); db.commit()
        return jsonify({'checkout_url': init_point_url, 'registration_id': registration_id})

    except Exception as e:
        traceback.print_exc()
        if registration_id:
             try: cursor.execute("DELETE FROM registrations WHERE id = ?", (registration_id,)); db.commit()
             except: pass
        return jsonify({'error': str(e)}), 500

# --- Rotas de Feedback, Webhook e Admin (sem alterações) ---
@app.route('/payment_feedback')
def payment_feedback():
    payment_id_arg = request.args.get('payment_id'); status_arg = request.args.get('status')
    external_reference_arg = request.args.get('external_reference')
    if not status_arg or not external_reference_arg:
        flash("Feedback incompleto.", "warning"); return redirect(url_for('index'))
    try:
        registration_id = int(external_reference_arg); db = get_db(); cursor = db.cursor()
        current_reg = cursor.execute("SELECT payment_status FROM registrations WHERE id = ?", (registration_id,)).fetchone()
        if current_reg and current_reg['payment_status'] == 'approved': flash("Pagamento já confirmado!", "info")
        elif status_arg == 'approved':
            cursor.execute("UPDATE registrations SET payment_id = ?, payment_status = ?, payment_approved_at = ? WHERE id = ?",
                           (payment_id_arg, 'approved', datetime.now(timezone.utc), registration_id)); db.commit()
            flash("Pagamento aprovado!", "success")
        else:
            cursor.execute("UPDATE registrations SET payment_id = ?, payment_status = ? WHERE id = ?",
                           (payment_id_arg, status_arg, registration_id)); db.commit()
            flash(f"Status do pagamento: {status_arg}.", "info" if status_arg == 'pending' else "danger")
    except Exception as e: traceback.print_exc(); flash("Erro ao processar feedback.", "danger")
    return redirect(url_for('index'))

@app.route('/webhook_mercadopago', methods=['POST'])
def webhook_mercadopago():
    data = request.get_json()
    if data and data.get("type") == "payment":
        payment_data_id = str(data.get("data", {}).get("id"))
        if not payment_data_id: return jsonify({'status': 'error', 'message': 'Payment ID não encontrado'}), 400
        try:
            payment_info = sdk.payment().get(payment_data_id)
            if not payment_info or payment_info.get("status") not in [200, 201]:
                return jsonify({'status': 'error', 'message': 'Falha ao obter info do pagamento MP'}), 500
            details = payment_info.get("response"); ext_ref = details.get("external_reference")
            status_api = details.get("status"); pay_id_api = str(details.get("id"))
            if ext_ref and status_api and pay_id_api:
                reg_id = int(ext_ref); db = get_db(); cursor = db.cursor()
                if status_api == "approved":
                    cursor.execute("UPDATE registrations SET payment_id = ?, payment_status = ?, payment_approved_at = ? WHERE id = ?",
                                   (pay_id_api, 'approved', datetime.now(timezone.utc), reg_id))
                else:
                    cursor.execute("UPDATE registrations SET payment_id = ?, payment_status = ? WHERE id = ? AND (payment_status != 'approved' OR payment_status IS NULL)",
                                   (pay_id_api, status_api, reg_id))
                db.commit()
        except Exception as e: traceback.print_exc(); return jsonify({'status': 'error', 'message': str(e)}), 500
    return jsonify({'status': 'received'}), 200

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form['username'] == ADMIN_USERNAME and request.form['password'] == ADMIN_PASSWORD:
            session['admin_logged_in'] = True; flash('Login sucesso!', 'success'); return redirect(url_for('admin_dashboard'))
        else: flash('Login inválido.', 'danger')
    return render_template('login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None); flash('Logout sucesso.', 'info'); return redirect(url_for('admin_login'))

@app.route('/admin')
def admin_dashboard():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    db = get_db(); cursor = db.cursor()
    cursor.execute("SELECT * FROM registrations ORDER BY created_at DESC")
    registrations = cursor.fetchall()
    return render_template('admin.html', registrations=registrations)

@app.route('/admin/get_registrations_data')
def admin_get_registrations_data():
    if not session.get('admin_logged_in'): return jsonify({'error': 'Não autorizado'}), 401
    db = get_db(); cursor = db.cursor()
    cursor.execute("SELECT * FROM registrations ORDER BY created_at DESC")
    registrations_raw = cursor.fetchall()
    registrations_list = []
    for row_proxy in registrations_raw:
        reg_dict = dict(row_proxy)
        reg_dict['created_at_formatted'] = format_datetime_local(reg_dict.get('created_at'))
        reg_dict['payment_approved_at_formatted'] = format_datetime_local(reg_dict.get('payment_approved_at'))
        registrations_list.append(reg_dict)
    return jsonify(registrations=registrations_list)

@app.route('/admin/send_link/<int:registration_id>', methods=['POST'])
def send_link_action(registration_id):
    if not session.get('admin_logged_in'): return jsonify({'error': 'Não autorizado'}), 401
    db = get_db(); cursor = db.cursor()
    reg_info = cursor.execute("SELECT whatsapp, payment_status FROM registrations WHERE id = ?", (registration_id,)).fetchone()
    if not reg_info: return jsonify({'success': False, 'error': 'Registro não encontrado'}), 404
    cursor.execute("UPDATE registrations SET link_sent = 1 WHERE id = ?", (registration_id,)); db.commit()
    return jsonify({'success': True, 'whatsapp': reg_info['whatsapp']})


if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)
