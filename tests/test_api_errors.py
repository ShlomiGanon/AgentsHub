from flask import Flask

from api.errors import ApiError, InternalError, InvalidInputError, RunFailureError, register_error_handlers


def _app():
    app = Flask(__name__)
    register_error_handlers(app)
    return app


def test_api_error_produces_the_one_fixed_shape():
    app = _app()

    @app.route("/boom")
    def boom():
        raise InvalidInputError("'x' is required", field="x")

    client = app.test_client()
    resp = client.get("/boom")

    assert resp.status_code == 400
    assert resp.get_json() == {"error_class": "invalid_input", "message": "'x' is required", "field": "x"}


def test_api_error_without_a_field_omits_the_key():
    app = _app()

    @app.route("/boom")
    def boom():
        raise InvalidInputError("bad request")

    resp = app.test_client().get("/boom")

    assert "field" not in resp.get_json()


def test_run_failure_error_has_its_own_class_and_status():
    app = _app()

    @app.route("/boom")
    def boom():
        raise RunFailureError("the main agent could not answer")

    resp = app.test_client().get("/boom")

    assert resp.status_code == 422
    assert resp.get_json()["error_class"] == "run_failure"


def test_unhandled_exception_becomes_a_generic_internal_error_never_leaking_details():
    app = _app()
    app.config["PROPAGATE_EXCEPTIONS"] = False
    app.testing = False  # a testing Flask app re-raises by default; force it through the handler

    @app.route("/boom")
    def boom():
        raise ValueError("some secret internal detail")

    resp = app.test_client().get("/boom")

    assert resp.status_code == 500
    body = resp.get_json()
    assert body["error_class"] == "internal_error"
    assert "secret" not in body["message"]
    assert body["message"] == "an internal error occurred"


def test_an_unmapped_route_returns_the_same_shape_not_werkzeugs_html_page():
    app = _app()

    resp = app.test_client().get("/does-not-exist")

    assert resp.status_code == 404
    assert resp.content_type.startswith("application/json")
    assert resp.get_json()["error_class"] == "invalid_input"


def test_a_wrong_method_returns_the_same_shape():
    app = _app()

    @app.route("/only-get", methods=["GET"])
    def only_get():
        return "ok"

    resp = app.test_client().post("/only-get")

    assert resp.status_code == 405
    assert resp.get_json()["error_class"] == "invalid_input"


def test_api_error_subclasses_carry_their_own_status_code():
    assert InvalidInputError("x").status_code == 400
    assert RunFailureError("x").status_code == 422
    assert InternalError("x").status_code == 500


def test_api_error_is_the_common_base():
    assert issubclass(InvalidInputError, ApiError)
    assert issubclass(RunFailureError, ApiError)
    assert issubclass(InternalError, ApiError)
