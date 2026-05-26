# ==============================================================================
# 인증 관련 Blueprint
# MFA, Country Challenge, 매니저 게이트/대시보드
# ==============================================================================

import random
import json
import uuid
from flask import Blueprint, render_template, jsonify, request, session, redirect, current_app, abort
from database import get_db_connection

auth_bp = Blueprint('auth', __name__)

MFA_COUNTRIES = [
    {'code': 'KR', 'name': 'South Korea'}, {'code': 'US', 'name': 'United States'},
    {'code': 'JP', 'name': 'Japan'}, {'code': 'CN', 'name': 'China'},
    {'code': 'GB', 'name': 'United Kingdom'}, {'code': '-99', 'name': 'France'},
    {'code': 'DE', 'name': 'Germany'}, {'code': 'IT', 'name': 'Italy'},
    {'code': 'CA', 'name': 'Canada'}, {'code': 'AU', 'name': 'Australia'},
    {'code': 'BR', 'name': 'Brazil'}, {'code': 'IN', 'name': 'India'},
    {'code': 'RU', 'name': 'Russia'}, {'code': 'ES', 'name': 'Spain'}
]

CHALLENGE_COUNTRIES = [
    'South Korea', 'United States of America', 'Japan', 'China',
    'United Kingdom', 'France', 'Germany', 'Italy', 'Canada',
    'Australia', 'Brazil', 'India', 'Russia', 'Spain', 'Mexico',
    'Indonesia', 'Turkey', 'Saudi Arabia', 'Argentina', 'South Africa'
]


# --- Manager Pages ---

@auth_bp.route('/manager')
def manager_gate():
    abort(404)  # /manager disabled 2026-05-27 (globe 3-country challenge was bypassable; feature unused)
    session.pop('is_admin', None)
    return render_template('globe.html')

@auth_bp.route('/manager/dashboard')
def manager_dashboard():
    abort(404)  # /manager disabled 2026-05-27 (globe 3-country challenge was bypassable; feature unused)
    if not session.get('challenge_passed'):
        return redirect('https://sosig.shop')
    return render_template('manager.html')

@auth_bp.route('/manager/settings')
def manager_settings():
    abort(404)  # /manager disabled 2026-05-27 (globe 3-country challenge was bypassable; feature unused)
    if not session.get('challenge_passed'):
        return redirect('https://sosig.shop')
    return render_template('settings.html')

@auth_bp.route('/api/gate/unlock', methods=['POST'])
def gate_unlock():
    abort(404)  # /manager disabled 2026-05-27 (globe 3-country challenge was bypassable; feature unused)
    if not session.get('challenge_passed'):
        return jsonify({"error": "unauthorized"}), 403
    session['is_admin'] = True
    return jsonify({"status": "unlocked"})


# --- MFA ---

@auth_bp.route('/api/auth/request-code', methods=['POST'])
def request_mfa_code():
    try:
        selection = random.sample(MFA_COUNTRIES, 3)
        codes = [c['code'] for c in selection]
        session_token = str(uuid.uuid4())

        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO verification_codes (session_id, countries) VALUES (%s, %s)",
                    (session_token, json.dumps(codes))
                )
            conn.commit()
        finally:
            conn.close()

        return jsonify({'status': 'sent', 'session_token': session_token})
    except Exception as e:
        current_app.logger.error(f'Error: {e}')
        return jsonify({'error': 'Internal server error'}), 500

@auth_bp.route('/api/auth/verify-code', methods=['POST'])
def verify_mfa_code():
    try:
        data = request.json
        session_token = data.get('session_token')
        user_countries = data.get('countries')

        if not session_token or not user_countries:
            return jsonify({'status': 'fail', 'message': 'Missing data'}), 400

        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT countries FROM verification_codes WHERE session_id = %s ORDER BY id DESC LIMIT 1",
                    (session_token,)
                )
                result = cursor.fetchone()
        finally:
            conn.close()

        if not result:
            return jsonify({'status': 'fail', 'message': 'Invalid session'}), 400

        stored_countries = json.loads(result['countries'])
        if user_countries == stored_countries:
            session['authenticated'] = True
            return jsonify({'status': 'unlocked'})
        else:
            return jsonify({'status': 'fail', 'message': 'Incorrect sequence'})
    except Exception as e:
        current_app.logger.error(f'Error: {e}')
        return jsonify({'error': 'Internal server error'}), 500


# --- Country Challenge ---

@auth_bp.route('/api/challenge/generate', methods=['POST'])
def generate_challenge():
    abort(404)  # /manager disabled 2026-05-27 (globe 3-country challenge was bypassable; feature unused)
    try:
        selected = random.sample(CHALLENGE_COUNTRIES, 3)
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT id FROM verification_codes WHERE id = 1")
                if cursor.fetchone():
                    cursor.execute(
                        "UPDATE verification_codes SET countries = %s, session_id = 'challenge' WHERE id = 1",
                        (json.dumps(selected),)
                    )
                else:
                    cursor.execute(
                        "INSERT INTO verification_codes (id, session_id, countries) VALUES (1, 'challenge', %s)",
                        (json.dumps(selected),)
                    )
            conn.commit()
            return jsonify({'status': 'success', 'countries': selected}), 200
        finally:
            conn.close()
    except Exception as e:
        current_app.logger.error(f'Error: {e}')
        return jsonify({'error': 'Internal server error'}), 500

@auth_bp.route('/api/challenge/current', methods=['GET'])
def get_current_challenge():
    abort(404)  # /manager disabled 2026-05-27 (globe 3-country challenge was bypassable; feature unused)
    try:
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT countries FROM verification_codes WHERE id = 1")
                result = cursor.fetchone()
                if result and result['countries']:
                    countries = json.loads(result['countries'])
                    return jsonify({'status': 'success', 'countries': countries}), 200
                else:
                    return jsonify({'status': 'none'}), 200
        finally:
            conn.close()
    except Exception as e:
        current_app.logger.error(f'Error: {e}')
        return jsonify({'error': 'Internal server error'}), 500

@auth_bp.route('/api/challenge/verify', methods=['POST'])
def verify_challenge():
    abort(404)  # /manager disabled 2026-05-27 (globe 3-country challenge was bypassable; feature unused)
    try:
        data = request.json
        clicked = data.get('countries', [])

        if len(clicked) != 3:
            return jsonify({'status': 'fail', 'message': 'Must click 3 countries'}), 400

        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT countries FROM verification_codes WHERE id = 1")
                result = cursor.fetchone()
                if not result or not result['countries']:
                    return jsonify({'status': 'fail', 'message': 'No challenge'}), 400
                challenge_countries = json.loads(result['countries'])
                if set(clicked) == set(challenge_countries):
                    session['challenge_passed'] = True
                    return jsonify({'status': 'success'}), 200
                else:
                    return jsonify({'status': 'fail', 'message': 'Wrong countries'}), 200
        finally:
            conn.close()
    except Exception as e:
        current_app.logger.error(f'Error: {e}')
        return jsonify({'error': 'Internal server error'}), 500
