from flask import Flask, request, jsonify
import subprocess
import os

app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    if request.headers.get('X-GitHub-Event') == 'push':
        if request.get_json()['ref'] == 'refs/heads/main':  # Check if it's the main branch
            try:
                # 1. Change to project directory
                os.chdir('/home/dev/Letterboxd-Fans-Finder')

                # 2. Pull the latest changes
                subprocess.run(['git', 'pull'], check=True)

                # 3. Install new requirements if requirements.txt has changed
                subprocess.run(['pip', 'install', '-r', 'requirements.txt'], check=True)

                # 4. Restart relevant services (adjust as needed)
                subprocess.run(['systemctl', 'restart', 'letterboxd-fans-finder'], check=True)
                subprocess.run(['systemctl', 'restart', 'letterboxd-fans-finder-worker'], check=True)

                return jsonify({'status': 'success'}), 200
            except subprocess.CalledProcessError as e:
                return jsonify({'status': 'error', 'message': str(e)}), 500
    else:
        return jsonify({'status': 'ignored'}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0')  # Listen on all interfaces