from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, jsonify, request

from .service import ProbeService, ProbeServiceError


def create_probe_blueprint(service: ProbeService) -> Blueprint:
    blueprint = Blueprint("network_probe", __name__, url_prefix="/api/network-probe")

    def client_ip() -> str:
        return service.normalize_ip(request.remote_addr)

    def bearer_token() -> str:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise ProbeServiceError("에이전트 인증 토큰이 없습니다.", 401)
        return header[7:].strip()

    def error_response(exc: ProbeServiceError):
        return jsonify({"error": str(exc)}), exc.status_code

    def payload() -> dict[str, Any]:
        value = request.get_json(silent=True)
        return value if isinstance(value, dict) else {}

    @blueprint.get("/status")
    def status():
        return jsonify(service.status_payload())

    @blueprint.get("/agents")
    def agents():
        return jsonify({"agents": service.list_agents()})

    @blueprint.post("/agents/register")
    def register_agent():
        try:
            return jsonify(service.register_agent(payload(), client_ip()))
        except ProbeServiceError as exc:
            return error_response(exc)

    @blueprint.get("/agents/<agent_id>/jobs/next")
    def next_job(agent_id: str):
        try:
            return jsonify(service.next_job(agent_id, bearer_token(), client_ip()))
        except ProbeServiceError as exc:
            return error_response(exc)

    @blueprint.post("/sessions")
    def create_session():
        value = payload()
        try:
            result = service.create_session(
                agent_id=str(value.get("agent_id", "")),
                direction=str(value.get("direction", "")),
                duration_seconds=int(value.get("duration_seconds", 0)),
                stream_count=int(value.get("stream_count", 0)),
            )
            return jsonify(result), 202
        except (TypeError, ValueError):
            return jsonify({"error": "TCP 측정 조건 형식이 올바르지 않습니다."}), 400
        except ProbeServiceError as exc:
            return error_response(exc)

    @blueprint.get("/sessions/<session_id>")
    def session_status(session_id: str):
        try:
            return jsonify(service.session_status(session_id))
        except ProbeServiceError as exc:
            return error_response(exc)

    @blueprint.post("/sessions/<session_id>/cancel")
    def cancel_session(session_id: str):
        try:
            return jsonify(service.cancel_session(session_id))
        except ProbeServiceError as exc:
            return error_response(exc)

    @blueprint.get("/sessions/<session_id>/control")
    def session_control(session_id: str):
        agent_id = request.args.get("agent_id", "")
        try:
            return jsonify(service.control_status(session_id, agent_id, bearer_token(), client_ip()))
        except ProbeServiceError as exc:
            return error_response(exc)

    @blueprint.post("/sessions/<session_id>/complete")
    def complete_phase(session_id: str):
        value = payload()
        agent_id = str(value.get("agent_id", ""))
        try:
            return jsonify(
                service.complete_agent_phase(session_id, agent_id, bearer_token(), client_ip(), value)
            )
        except ProbeServiceError as exc:
            return error_response(exc)

    @blueprint.get("/results/<session_id>.json")
    def result_json(session_id: str):
        try:
            path = service.result_path_for(session_id)
        except ProbeServiceError as exc:
            return error_response(exc)
        return Response(
            path.read_text(encoding="utf-8"),
            mimetype="application/json",
            headers={"Content-Disposition": f'attachment; filename="network-probe-{session_id}.json"'},
        )

    return blueprint
