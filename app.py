from flask import Flask, request, jsonify, render_template
from capture import capture_site

app = Flask(__name__)

@app.get("/")
def home():
    return render_template("index.html")

@app.get("/capture")
def capture():
    url = request.args.get("url")
    if not url:
        return {"error":"missing url"}, 400

    result = capture_site(url)

    return jsonify({
        "url": url,
        "title": result.get("title"),
        "assets_count": len(result.get("layout", [])),
        "screenshot_size": result.get("screenshot_size")
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
