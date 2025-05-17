import os
import sqlite3
import mercadopago
import json
import traceback
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash, Response # Adicionado Response
from datetime import datetime, timezone
import pytz
from dotenv import load_dotenv
import csv # << NOVO: Para gerar CSV
import io  # << NOVO: Para stream de dados em memória

# Carrega variáveis do arquivo .env (se existir)
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY')
if not app.secret_key:
    raise ValueError("FLASK_SECRET_KEY não configurada. Verifique suas variáveis de ambiente ou .env.")

DATABASE = 'database.db' # O SQLite será criado na raiz do projeto

# Credenciais do Mercado Pago
MP_ACCESS_TOKEN = os.getenv('MP_ACCESS_TOKEN_PROD')
MP_PUBLIC_KEY = os.getenv('MP_PUBLIC_KEY_PROD')

# Credenciais do Admin
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'DKFUTEADM') 
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'kevAmor132@@@senhaA')

if not MP_ACCESS_TOKEN or not MP_PUBLIC_KEY:
    raise ValueError("MP_ACCESS_TOKEN e MP_PUBLIC_KEY devem ser configurados nas variáveis de ambiente ou .env.")
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)


# --- Funções do Banco de Dados ---
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

# --- Filtro Jinja2 e Função Auxiliar para Fuso Horário ---
def format_datetime_local(value, tz_name='America/Sao_Paulo', fmt='%d/%m/%Y %H:%M:%S'):
    if value == "now":
        now_utc = datetime.now(timezone.utc)
        try:
            local_tz = pytz.timezone(tz_name)
            local_now = now_utc.astimezone(local_tz)
            return local_now.strftime(fmt)
        except Exception as e:
            print(f"DEBUG: Erro ao formatar 'now' para fuso {tz_name}: {e}")
            return now_utc.strftime(fmt) + " (UTC)"

    if not value: return ""
    utc_dt = None
    if isinstance(value, str):
        try:
            value_clean = value.split('.')[0]
            if len(value_clean) == 19: # Formato YYYY-MM-DD HH:MM:SS
                 utc_dt = datetime.strptime(value_clean, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
            else: # Tenta ISO format
                utc_dt = datetime.fromisoformat(value.replace('Z', '+00:00')).replace(tzinfo=timezone.utc)
        except ValueError:
            try: # Fallback para string
                dt_naive = datetime.strptime(value_clean, '%Y-%m-%d %H:%M:%S')
                utc_dt = dt_naive.replace(tzinfo=timezone.utc)
            except ValueError:
                print(f"DEBUG: Não foi possível parsear a string de data '{value}'")
                return value
    elif isinstance(value, datetime):
        if value.tzinfo is None:
            utc_dt = value.replace(tzinfo=timezone.utc)
        else:
            utc_dt = value.astimezone(timezone.utc)
    else:
        return value

    if utc_dt:
        try:
            local_tz = pytz.timezone(tz_name)
            local_dt = utc_dt.astimezone(local_tz)
            return local_dt.strftime(fmt)
        except pytz.exceptions.UnknownTimeZoneError:
            print(f"DEBUG: Fuso horário desconhecido '{tz_name}'")
            return utc_dt.strftime(fmt) + " (UTC-Fuso?)"
        except Exception as e:
            print(f"DEBUG: Erro ao converter fuso: {e}")
            return utc_dt.strftime(fmt) + " (UTC-Conv?)"
    return value
app.jinja_env.filters['datetime_local'] = format_datetime_local
# --- FIM Filtro Jinja2 ---

# --- Rotas da Aplicação ---
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
        print(f"LOG: Novo registro ID: {registration_id} para {name}, time: {time_torce}, email: {email_usuario}")

        APP_BASE_URL = os.getenv("APP_BASE_URL")
        if not APP_BASE_URL:
            print("LOG CRÍTICO: APP_BASE_URL não está definida nas variáveis de ambiente ou .env! Webhooks e Back URLs não funcionarão para o Mercado Pago.")
            return jsonify({'error': 'Configuração crítica do servidor ausente (APP_BASE_URL). O pagamento não pode prosseguir.'}), 500

        clean_whatsapp = "".join(filter(str.isdigit, whatsapp))
        phone_details = {}
        if len(clean_whatsapp) >= 10:
            phone_details["area_code"] = clean_whatsapp[:2]
            phone_details["number"] = clean_whatsapp[2:]
        
        payer_email_para_mp = email_usuario if email_usuario and "@" in email_usuario else f"user_{registration_id}@dkfute.com"

        preference_data = {
            "items": [{"title": "Acesso à Transmissão de Futebol", "quantity": 1, "unit_price": 3.00, "currency_id": "BRL"}],
            "payer": {"name": name, "phone": phone_details if phone_details else None, "email": payer_email_para_mp},
            "back_urls": {
                "success": f"{APP_BASE_URL}{url_for('payment_feedback')}",
                "failure": f"{APP_BASE_URL}{url_for('payment_feedback')}",
                "pending": f"{APP_BASE_URL}{url_for('payment_feedback')}"
            },
            "notification_url": f"{APP_BASE_URL}{url_for('webhook_mercadopago')}",
            "external_reference": str(registration_id)
        }
        if not preference_data["payer"]["phone"]: del preference_data["payer"]["phone"]
        
        print(f"LOG: Dados para MP (Base URL: {APP_BASE_URL}): {json.dumps(preference_data, indent=2, ensure_ascii=False)}")
        preference_response = sdk.preference().create(preference_data)
        
        print(f"LOG: Resposta MP (Criação Preferência): {json.dumps(preference_response, indent=2, ensure_ascii=False)}")
        if not preference_response or preference_response.get("status") not in [200, 201]:
            error_details = preference_response.get("response", {}) if isinstance(preference_response, dict) else {}
            error_message = error_details.get("message", "Erro desconhecido ao criar preferência no MP.")
            causes = error_details.get("cause", [])
            if not causes and isinstance(error_details.get("causes"), list): causes = error_details.get("causes")
            if causes and isinstance(causes, list) and len(causes) > 0 and isinstance(causes[0], dict):
                 error_message += " Causa: " + causes[0].get("description", str(causes[0]))
            print(f"LOG ERRO MP: {error_message} | Resposta completa: {json.dumps(preference_response, indent=2, ensure_ascii=False)}")
            raise Exception(f"Erro API MP: {error_message}")
        
        preference_details = preference_response.get("response")
        preference_id_mp = preference_details.get('id')
        init_point_url = preference_details.get('init_point')
        if not preference_id_mp or not init_point_url:
            raise Exception("ID da preferência ou init_point não encontrados na resposta do MP.")
        cursor.execute("UPDATE registrations SET preference_id = ?, payment_status = ? WHERE id = ?",
                       (preference_id_mp, 'pending_payment', registration_id)); db.commit()
        print(f"LOG: Preferência {preference_id_mp} criada para registro {registration_id}.")
        return jsonify({'checkout_url': init_point_url, 'registration_id': registration_id})

    except Exception as e:
        print(f"LOG ERRO GERAL em /create_preference para reg_id {registration_id}:")
        traceback.print_exc()
        if registration_id:
             try: 
                 cursor.execute("DELETE FROM registrations WHERE id = ?", (registration_id,)); 
                 db.commit()
                 print(f"LOG: Registro {registration_id} deletado devido à falha na criação da preferência.")
             except Exception as db_err:
                 print(f"LOG: Erro ao tentar deletar registro {registration_id}: {db_err}")
        return jsonify({'error': f"Ocorreu um erro interno: {str(e)}"}), 500

@app.route('/payment_feedback')
def payment_feedback():
    payment_id_arg = request.args.get('payment_id'); status_arg = request.args.get('status')
    external_reference_arg = request.args.get('external_reference')
    print(f"LOG: Payment Feedback Recebido - Args: {request.args}")
    if not status_arg or not external_reference_arg:
        flash("Informações de feedback de pagamento incompletas.", "warning"); return redirect(url_for('index'))
    try:
        registration_id = int(external_reference_arg); db = get_db(); cursor = db.cursor()
        current_reg = cursor.execute("SELECT payment_status FROM registrations WHERE id = ?", (registration_id,)).fetchone()
        if current_reg and current_reg['payment_status'] == 'approved':
            print(f"LOG: Feedback para reg {registration_id} já aprovado. Nada a fazer.")
            flash("Seu pagamento já foi confirmado!", "info")
        elif status_arg == 'approved':
            cursor.execute("UPDATE registrations SET payment_id = ?, payment_status = ?, payment_approved_at = ? WHERE id = ?",
                           (payment_id_arg, 'approved', datetime.now(timezone.utc), registration_id)); db.commit()
            print(f"LOG: Reg {registration_id} atualizado para APROVADO via feedback.")
            flash("Pagamento aprovado! Em breve você receberá o link.", "success")
        elif status_arg == 'pending':
            cursor.execute("UPDATE registrations SET payment_id = ?, payment_status = ? WHERE id = ? AND (payment_status != 'approved' OR payment_status IS NULL)",
                           (payment_id_arg, 'pending_confirmation', registration_id)); db.commit()
            print(f"LOG: Reg {registration_id} atualizado para PENDENTE via feedback.")
            flash(f"Seu pagamento (ID: {payment_id_arg}) está pendente de confirmação.", "info")
        else: 
            cursor.execute("UPDATE registrations SET payment_id = ?, payment_status = ? WHERE id = ? AND (payment_status != 'approved' OR payment_status IS NULL)",
                           (payment_id_arg, status_arg, registration_id)); db.commit()
            print(f"LOG: Reg {registration_id} atualizado para status '{status_arg}' via feedback.")
            flash(f"Ocorreu um problema com seu pagamento (Status: {status_arg}). Tente novamente ou contate o suporte.", "danger")
    except ValueError: flash("Referência externa inválida no feedback de pagamento.", "danger")
    except Exception as e:
        print(f"LOG ERRO em /payment_feedback:"); traceback.print_exc()
        flash("Ocorreu um erro ao processar o retorno do seu pagamento.", "danger")
    return redirect(url_for('index'))

@app.route('/webhook_mercadopago', methods=['POST'])
def webhook_mercadopago():
    data = request.get_json()
    print(f"LOG: Webhook Recebido - Dados: {json.dumps(data, indent=2, ensure_ascii=False)}")
    if data and data.get("type") == "payment":
        payment_data_id = str(data.get("data", {}).get("id"))
        if not payment_data_id:
            print("LOG Webhook: 'data.id' (payment_id) não encontrado.")
            return jsonify({'status': 'error', 'message': 'Payment ID (data.id) não encontrado'}), 400
        try:
            print(f"LOG Webhook: Consultando pagamento ID: {payment_data_id} na API MP...")
            payment_info_response = sdk.payment().get(payment_data_id)
            print(f"LOG Webhook: Resposta Consulta Pagamento - {json.dumps(payment_info_response, indent=2, ensure_ascii=False)}")
            if not payment_info_response or payment_info_response.get("status") not in [200, 201]:
                error_msg = payment_info_response.get("response", {}).get("message", "Falha API MP")
                print(f"LOG Webhook ERRO: Falha ao obter info do pagamento {payment_data_id}. Status API: {payment_info_response.get('status')}. Msg: {error_msg}")
                return jsonify({'status': 'error', 'message': f'Falha API MP: {error_msg}'}), 500
            payment_details = payment_info_response.get("response")
            if not payment_details or not isinstance(payment_details, dict):
                 print(f"LOG Webhook ERRO: 'response' formato inválido {payment_data_id}")
                 return jsonify({'status': 'error', 'message': 'Formato resposta MP inválido'}), 500
            external_reference = payment_details.get("external_reference"); payment_status_from_api = payment_details.get("status")
            retrieved_payment_id_from_api = str(payment_details.get("id"))
            if external_reference and payment_status_from_api and retrieved_payment_id_from_api:
                registration_id = int(external_reference); db = get_db(); cursor = db.cursor()
                current_reg = cursor.execute("SELECT payment_status FROM registrations WHERE id = ?", (registration_id,)).fetchone()
                if current_reg and current_reg['payment_status'] == 'approved' and payment_status_from_api != 'approved':
                    print(f"LOG Webhook: Reg {registration_id} já aprovado. Ignorando atualização para '{payment_status_from_api}'.")
                elif payment_status_from_api == "approved":
                    cursor.execute("UPDATE registrations SET payment_id = ?, payment_status = ?, payment_approved_at = ? WHERE id = ?",
                                   (retrieved_payment_id_from_api, 'approved', datetime.now(timezone.utc), registration_id)); db.commit()
                    print(f"LOG Webhook: Reg {registration_id} atualizado para APROVADO para pag {retrieved_payment_id_from_api}.")
                else:
                    cursor.execute("UPDATE registrations SET payment_id = ?, payment_status = ? WHERE id = ? AND (payment_status != 'approved' OR payment_status IS NULL)",
                                   (retrieved_payment_id_from_api, payment_status_from_api, registration_id)); db.commit()
                    print(f"LOG Webhook: Reg {registration_id} atualizado para '{payment_status_from_api}' para pag {retrieved_payment_id_from_api}.")
            else: print(f"LOG Webhook ERRO: Dados incompletos. ExtRef: {external_reference}, Status: {payment_status_from_api}, PayID: {retrieved_payment_id_from_api}")
        except Exception as e:
            print(f"LOG ERRO CRÍTICO webhook para pag {payment_data_id}:"); traceback.print_exc()
            return jsonify({'status': 'error', 'message': f"Erro interno webhook: {str(e)}"}), 500
    return jsonify({'status': 'received'}), 200

# --- Rotas do Admin ---
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True; flash('Login realizado com sucesso!', 'success'); return redirect(url_for('admin_dashboard'))
        else: flash('Usuário ou senha inválidos.', 'danger')
    return render_template('login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None); flash('Logout realizado com sucesso.', 'info'); return redirect(url_for('admin_login'))

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
    if reg_info['payment_status'] != 'approved':
        return jsonify({'success': False, 'error': 'Pagamento não aprovado para este registro.'}), 403
    cursor.execute("UPDATE registrations SET link_sent = 1 WHERE id = ?", (registration_id,)); db.commit()
    print(f"LOG: Link marcado como enviado para registro {registration_id}")
    return jsonify({'success': True, 'whatsapp': reg_info['whatsapp']})

# <<< NOVA ROTA PARA DOWNLOAD CSV >>>
@app.route('/admin/download_registrations_csv')
def download_registrations_csv():
    if not session.get('admin_logged_in'):
        flash("Acesso não autorizado.", "danger")
        return redirect(url_for('admin_login'))

    try:
        db = get_db()
        cursor = db.cursor()
        # Selecionar todas as colunas que você quer no CSV
        cursor.execute("SELECT id, name, whatsapp, time_torce, email, payment_status, payment_id, created_at, payment_approved_at, link_sent FROM registrations ORDER BY created_at DESC")
        registrations = cursor.fetchall()

        si = io.StringIO()
        cw = csv.writer(si)

        headers = [
            'ID', 'Nome', 'WhatsApp', 'Time que Torce', 'Email', 
            'Status Pagamento', 'ID Pagamento MP', 
            'Data Cadastro', 'Data Aprovação Pag.', 'Link Enviado'
        ]
        cw.writerow(headers)

        for reg_proxy in registrations:
            reg = dict(reg_proxy) # Converter sqlite3.Row para dict para fácil acesso
            created_at_local = format_datetime_local(reg.get('created_at'))
            payment_approved_at_local = format_datetime_local(reg.get('payment_approved_at'))
            link_sent_text = "Sim" if reg.get('link_sent') == 1 else "Não"

            cw.writerow([
                reg.get('id'),
                reg.get('name'),
                reg.get('whatsapp'),
                reg.get('time_torce'),
                reg.get('email', ''), # Usar get com default para evitar KeyError se a coluna for opcional
                reg.get('payment_status'),
                reg.get('payment_id', ''),
                created_at_local,
                payment_approved_at_local,
                link_sent_text
            ])
        
        output = si.getvalue()
        si.close()

        return Response(
            output,
            mimetype="text/csv",
            headers={"Content-disposition":
                     "attachment; filename=cadastros_dkfute.csv"}
        )
    except Exception as e:
        print("LOG ERRO ao gerar CSV:")
        traceback.print_exc()
        flash("Erro ao gerar o arquivo CSV.", "danger")
        return redirect(url_for('admin_dashboard'))
# <<< FIM DA NOVA ROTA >>>

if __name__ == '__main__':
    init_db() 
    app.run(debug=True, port=5000)
