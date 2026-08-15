from flask import Flask, jsonify

app = Flask(__name__)


@app.get("/")
def home():
    return jsonify({
        "service": "StudioTech BI Report Engine",
        "status": "running"
    })


@app.get("/health")
def health():
    return jsonify({
        "status": "healthy"
    })


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=8000
    )
