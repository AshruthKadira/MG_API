# server/app.py
from flask import Flask, request, Response
from routes import routes

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False


# Register all route blueprints here
app.register_blueprint(routes)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5020, debug=True)