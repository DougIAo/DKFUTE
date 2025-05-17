import os
import sqlite3
import mercadopago
import json
import traceback
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from datetime import datetime, timezone # << MODIFICADO: Adicionado timezone
import pytz # << NOVO: Adicionado pytz
from dotenv import load_dotenv

# Carrega variáveis do arquivo .env (se existir)
load_dotenv()

# Cria a instância da aplicação Flask ANTES de definir rotas
app = Flask(__name__)

# Configura a chave secreta para sessions e flash messages
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'uma_chave_secreta_bem_aleatoria_para_testes_dhgsfdyg')

# --- Configurações da Aplicação ---
DATABASE = 'database.db'

# Credenciais do Mercado Pago (PRODUÇÃO)
MP_ACCESS_TOKEN = os.getenv('MP_ACCESS_TOKEN_PROD', 'APP_USR-871748903125073-051610-2f3e4b7630d571fdf140df104feaeb62-2440765345')
MP_PUBLIC_KEY = os.getenv('MP_PUBLIC_KEY_PROD', 'APP_USR-97238d58-f48a-44f9-8b72-40c8655a2b5a')

# Credenciais do Admin
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'senhaforte123')

# Validação e Inicialização do SDK do Mercado Pago
if not MP_ACCESS_TOKEN:
    raise ValueError("MP_ACCESS_TOKEN não está configurado. Verifique suas variáveis de ambiente ou o código.")
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

# --- Filtro Jinja2 para Fuso Horário ---
def format_datetime_local(value, tz_name='America/Sao_Paulo', fmt='%d/%m/%Y %H:%M:%S'):
    """
    Converte uma string de data/hora (assumida como UTC) ou um objeto datetime
    para um fuso horário local e formata.
    Lembre-se de ajustar 'America/Sao_Paulo' para o seu fuso horário.
    """
    if not value:
        return ""

    utc_dt = None
    if isinstance(value, str):
        try:
            value_clean = value.split('.')[0] # Remove microssegundos se houver
            if len(value_clean) == 19: # Formato 'YYYY-MM-DD HH:MM:SS'
                 utc_dt = datetime.strptime(value_clean, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
            else: # Tenta um parse mais genérico
                utc_dt = datetime.fromisoformat(value.replace('Z', '+00:00')).replace(tzinfo=timezone.utc)
        except ValueError:
            try: # Última tentativa para string
                dt_naive = datetime.strptime(value_clean, '%Y-%m-%d %H:%M:%S')
                utc_dt = dt_naive.replace(tzinfo=timezone.utc)
            except ValueError:
                print(f"Debug: Não foi possível parsear a string de data '{value}' para datetime UTC.")
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
            print(f"Debug: Fuso horário desconhecido '{tz_name}'. Retornando UTC formatado.")
            return utc_dt.strftime(fmt) + " (UTC - Erro Fuso)"
        except Exception as e:
            print(f"Debug: Erro ao converter fuso horário para '{tz_name}': {e}. Retornando UTC formatado.")
            return utc_dt.strftime(fmt) + f" (UTC - Erro Conv: {e})"
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
    email = data.get('email')

    if not name or not whatsapp:
        return jsonify({'error': 'Nome e WhatsApp são obrigatórios'}), 400

    db = get_db()
    cursor = db.cursor()
    registration_id = None

    try:
        # created_at será definido pelo DEFAULT CURRENT_TIMESTAMP do SQLite (provavelmente UTC)
        cursor.execute(
            "INSERT INTO registrations (name, whatsapp, email, payment_status) VALUES (?, ?, ?, ?)",
            (name, whatsapp, email, 'pending_creation')
        )
        db.commit()
        registration_id = cursor.lastrowid
        print(f"Novo registro ID: {registration_id} criado para {name}")

        clean_whatsapp = "".join(filter(str.isdigit, whatsapp))
        phone_details = {}
        if len(clean_whatsapp) >= 10:
            phone_details["area_code"] = clean_whatsapp[:2]
            phone_details["number"] = clean_whatsapp[2:]
        else:
            print(f"Número de WhatsApp ({whatsapp}) inválido/curto. Não será incluído nos dados do pagador.")

        # !!! IMPORTANTE: SUBSTITUA PELO SEU URL DO NGROK ATUAL !!!
        NGROK_BASE_URL = "https://e7b4-190-102-47-31.ngrok-free.app" # COLOQUE SEU URL DO NGROK AQUI

        preference_data = {
            "items": [
                {
                    "title": "Acesso à Transmissão de Futebol",
                    "quantity": 1,
                    "unit_price": 3.00,
                    "currency_id": "BRL"
                }
            ],
            "payer": {
                "name": name,
                "phone": phone_details if phone_details else None,
                "email": email if email else f"user_{registration_id}@example.com"
            },
            "back_urls": {
                "success": f"{NGROK_BASE_URL}{url_for('payment_feedback')}",
                "failure": f"{NGROK_BASE_URL}{url_for('payment_feedback')}",
                "pending": f"{NGROK_BASE_URL}{url_for('payment_feedback')}"
            },
            "notification_url": f"{NGROK_BASE_URL}{url_for('webhook_mercadopago')}",
            "external_reference": str(registration_id)
        }

        if preference_data["payer"]["phone"] is None:
            del preference_data["payer"]["phone"]

        print("--- DADOS ENVIADOS PARA CRIAR PREFERÊNCIA (Checkout Pro) ---")
        print(json.dumps(preference_data, indent=2, ensure_ascii=False))
        print("---------------------------------------------------------")

        preference_response = sdk.preference().create(preference_data)
        
        print("--- RESPOSTA COMPLETA DA API DO MERCADO PAGO (Criação Preferência) ---")
        print(json.dumps(preference_response, indent=2, ensure_ascii=False))
        print("---------------------------------------------------------------------")

        if not preference_response or not isinstance(preference_response, dict):
            raise Exception(f"Resposta inesperada da API ao criar preferência: {preference_response}")

        response_status_code = preference_response.get("status")

        if response_status_code in [200, 201]:
            preference_details = preference_response.get("response")
            if not preference_details or not isinstance(preference_details, dict):
                raise Exception(f"Chave 'response' não encontrada ou formato inválido na resposta da criação de preferência: {preference_response.get('response')}")
            
            preference_id_mp = preference_details.get('id')
            init_point_url = preference_details.get('init_point')

            if not preference_id_mp or not init_point_url:
                missing_keys = []
                if not preference_id_mp: missing_keys.append("'id' da preferência")
                if not init_point_url: missing_keys.append("'init_point'")
                raise Exception(f"Chave(s) {', '.join(missing_keys)} não encontrada(s) em 'response': {preference_details}")

            cursor.execute(
                "UPDATE registrations SET preference_id = ?, payment_status = ? WHERE id = ?",
                (preference_id_mp, 'pending_payment', registration_id)
            )
            db.commit()
            print(f"Preferência {preference_id_mp} criada com sucesso. URL de checkout: {init_point_url}")
            
            return jsonify({'checkout_url': init_point_url, 'registration_id': registration_id})
        else:
            error_message = "Erro desconhecido da API do Mercado Pago ao criar preferência."
            response_content = preference_response.get("response")
            if response_content and isinstance(response_content, dict):
                error_message = response_content.get("message", error_message)
                causes = response_content.get("cause", [])
                if not causes and isinstance(response_content.get("causes"), list):
                    causes = response_content.get("causes")
                if causes:
                    error_message += " | Causas: " + json.dumps(causes)
            elif isinstance(preference_response.get("message"), str):
                error_message = preference_response.get("message")
            
            raise Exception(f"Erro da API MP (SDK Status: {response_status_code}): {error_message} - Resposta Completa: {json.dumps(preference_response, indent=2, ensure_ascii=False)}")

    except Exception as e:
        print(f"!!!!! ERRO GERAL AO CRIAR PREFERÊNCIA !!!!!")
        traceback.print_exc()
        print(f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")

        if registration_id:
             print(f"Tentando deletar registro {registration_id} devido à falha na criação da preferência.")
             try:
                 cursor.execute("DELETE FROM registrations WHERE id = ?", (registration_id,))
                 db.commit()
                 print(f"Registro {registration_id} deletado.")
             except Exception as db_err:
                 print(f"Erro ao tentar deletar registro {registration_id}: {db_err}")
        
        return jsonify({'error': str(e)}), 500


@app.route('/payment_feedback')
def payment_feedback():
    payment_id_arg = request.args.get('payment_id')
    status_arg = request.args.get('status')
    external_reference_arg = request.args.get('external_reference')
    preference_id_arg = request.args.get('preference_id')

    print("--- Payment Feedback Recebido (Back URL) ---")
    print(f"Payment ID: {payment_id_arg}, Status: {status_arg}, External Ref: {external_reference_arg}, Preference ID: {preference_id_arg}")
    print(f"Todos os args: {request.args}")
    print("--------------------------------------------")

    if not status_arg or not external_reference_arg:
        flash("Informações de feedback de pagamento incompletas.", "warning")
        return redirect(url_for('index'))

    try:
        registration_id = int(external_reference_arg)
        db = get_db()
        cursor = db.cursor()
        
        current_reg = cursor.execute("SELECT payment_status FROM registrations WHERE id = ?", (registration_id,)).fetchone()
        
        if current_reg and current_reg['payment_status'] == 'approved':
            print(f"Feedback para registro {registration_id} (pag: {payment_id_arg}) já está 'approved'. Não fazendo nada no feedback.")
            flash("Seu pagamento já foi confirmado!", "info")
        elif status_arg == 'approved':
            cursor.execute(
                "UPDATE registrations SET payment_id = ?, payment_status = ?, payment_approved_at = ? WHERE id = ?",
                (payment_id_arg, 'approved', datetime.now(timezone.utc), registration_id) # << MODIFICADO: Usando timezone.utc
            )
            db.commit()
            flash(f"Pagamento aprovado (ID: {payment_id_arg})! Em breve você receberá o link.", "success")
        elif status_arg == 'pending':
            cursor.execute(
                "UPDATE registrations SET payment_id = ?, payment_status = ? WHERE id = ?",
                (payment_id_arg, 'pending_confirmation', registration_id)
            )
            db.commit()
            flash(f"Seu pagamento (ID: {payment_id_arg}) está pendente de confirmação.", "info")
        else:
            cursor.execute(
                "UPDATE registrations SET payment_id = ?, payment_status = ? WHERE id = ?",
                (payment_id_arg, status_arg, registration_id)
            )
            db.commit()
            flash(f"Ocorreu um problema com seu pagamento (Status: {status_arg}). Tente novamente ou contate o suporte.", "danger")
        
    except ValueError:
        flash("Referência externa inválida no feedback de pagamento.", "danger")
    except Exception as e:
        print(f"Erro no processamento do feedback de pagamento: {e}")
        traceback.print_exc()
        flash("Ocorreu um erro ao processar o retorno do seu pagamento.", "danger")
        
    return redirect(url_for('index'))


@app.route('/webhook_mercadopago', methods=['POST'])
def webhook_mercadopago():
    data = request.get_json()
    print("--- Webhook Recebido ---")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print("------------------------")

    if data and data.get("type") == "payment":
        payment_data_id_from_webhook = str(data.get("data", {}).get("id"))
        
        if not payment_data_id_from_webhook:
            print("Webhook: 'data.id' (payment_id) não encontrado no payload.")
            return jsonify({'status': 'error', 'message': 'Payment ID (data.id) not found in webhook payload'}), 400
        
        try:
            print(f"Webhook: Consultando detalhes do pagamento ID: {payment_data_id_from_webhook} na API MP...")
            payment_info_response = sdk.payment().get(payment_data_id_from_webhook)
            
            print("--- Resposta da Consulta do Pagamento (Webhook) ---")
            print(json.dumps(payment_info_response, indent=2, ensure_ascii=False))
            print("----------------------------------------------------")

            if not payment_info_response or payment_info_response.get("status") not in [200, 201]:
                error_msg = payment_info_response.get("response", {}).get("message", "Falha ao obter informações do pagamento via API")
                print(f"Webhook: Falha ao obter informações do pagamento {payment_data_id_from_webhook}. Status API: {payment_info_response.get('status')}. Msg: {error_msg}")
                return jsonify({'status': 'error', 'message': f'Failed to get payment info from MP API: {error_msg}'}), 500

            payment_details = payment_info_response.get("response")
            if not payment_details or not isinstance(payment_details, dict):
                 print(f"Webhook: 'response' não encontrado ou formato inválido na consulta do pagamento {payment_data_id_from_webhook}")
                 return jsonify({'status': 'error', 'message': 'Payment info response format error from MP API'}), 500

            external_reference = payment_details.get("external_reference")
            payment_status_from_api = payment_details.get("status")
            retrieved_payment_id_from_api = str(payment_details.get("id"))

            if external_reference and payment_status_from_api and retrieved_payment_id_from_api:
                registration_id = int(external_reference)
                db = get_db()
                cursor = db.cursor()
                
                if payment_status_from_api == "approved":
                    cursor.execute(
                        "UPDATE registrations SET payment_id = ?, payment_status = ?, payment_approved_at = ? WHERE id = ?",
                        (retrieved_payment_id_from_api, 'approved', datetime.now(timezone.utc), registration_id) # << MODIFICADO: Usando timezone.utc
                    )
                    db.commit()
                    print(f"Registro {registration_id} ATUALIZADO PARA APROVADO via webhook para pagamento {retrieved_payment_id_from_api}.")
                else:
                    cursor.execute(
                        "UPDATE registrations SET payment_id = ?, payment_status = ? WHERE id = ? AND (payment_status != 'approved' OR payment_status IS NULL)",
                        (retrieved_payment_id_from_api, payment_status_from_api, registration_id)
                    )
                    db.commit()
                    print(f"Registro {registration_id} atualizado para status '{payment_status_from_api}' via webhook para pagamento {retrieved_payment_id_from_api}.")
            else:
                print(f"Webhook: Dados incompletos na consulta do pagamento. ExtRef: {external_reference}, Status API: {payment_status_from_api}, PayID API: {retrieved_payment_id_from_api}")

        except Exception as e:
            print(f"Erro CRÍTICO ao processar webhook para pagamento {payment_data_id_from_webhook}: {e}")
            traceback.print_exc()
            return jsonify({'status': 'error', 'message': str(e)}), 500

    return jsonify({'status': 'received'}), 200

# --- Rotas do Admin ---
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            flash('Login realizado com sucesso!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Usuário ou senha inválidos.', 'danger')
    return render_template('login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    flash('Logout realizado com sucesso.', 'info')
    return redirect(url_for('admin_login'))

@app.route('/admin')
def admin_dashboard():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM registrations ORDER BY created_at DESC")
    registrations = cursor.fetchall()
    return render_template('admin.html', registrations=registrations)

@app.route('/admin/send_link/<int:registration_id>', methods=['POST'])
def send_link_action(registration_id):
    if not session.get('admin_logged_in'):
        return jsonify({'error': 'Não autorizado'}), 401

    db = get_db()
    cursor = db.cursor()
    reg_info = cursor.execute("SELECT whatsapp, payment_status FROM registrations WHERE id = ?", (registration_id,)).fetchone()

    if not reg_info:
        flash(f'Registro ID {registration_id} não encontrado.', 'danger')
        return jsonify({'success': False, 'error': 'Registro não encontrado'}), 404
    
    cursor.execute("UPDATE registrations SET link_sent = 1 WHERE id = ?", (registration_id,))
    db.commit()
    
    flash(f'Link para ID {registration_id} marcado como enviado.', 'success')
    return jsonify({'success': True, 'whatsapp': reg_info['whatsapp']})


# --- Ponto de Entrada da Aplicação ---
if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)
