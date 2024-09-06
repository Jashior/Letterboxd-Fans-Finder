import logging
import os
import subprocess
from flask import Flask, jsonify, render_template, request
from rq import Queue
from worker import conn 
from tasks import scrape_letterboxd_favorites, scrape_letterboxd_fans, get_all_fans_of_favorites

app = Flask(__name__, template_folder='frontend')
q = Queue(connection=conn)

@app.route('/', methods=['GET'])  # Remove POST handling
def index():
    return render_template('index.html')  # No search results needed

@app.route('/all_fans', methods=['POST'])
def get_all_fans_form():
    data = request.get_json()
    username = data.get('username')
    if not username:
        return jsonify({'error': 'Username is required'}), 400

    job = q.enqueue(get_all_fans_of_favorites, username) 
    return jsonify({'job_id': job.id})

@app.route('/all_fans/<username>')
def get_all_fans_api(username):
    job = q.enqueue(get_all_fans_of_favorites, username) 
    return jsonify({'job_id': job.id})


    
# Route to get a user's favorite movies
@app.route('/favorites/<username>')
def get_favorites(username):
    job = q.enqueue(scrape_letterboxd_favorites, username)
    return jsonify({'job_id': job.id})

# Route to get fans for a specific list of favorites 
@app.route('/fans/<job_id>')
def get_fans(job_id):
    job = q.fetch_job(job_id)
    if job.is_finished:
        favorites = job.result
        fan_job = q.enqueue(scrape_letterboxd_fans, favorites)
        return jsonify({'fan_job_id': fan_job.id})
    else:
        return jsonify({'status': job.get_status()})

# Route for retrieving results for a job (GET)
@app.route('/results/<job_id>')
def get_results(job_id):
    job = q.fetch_job(job_id)
    if job.is_finished:
        result = job.result
        return jsonify({'status': 'finished', 'result': result})
    else:
        return jsonify({'status': job.get_status()})
    
#webhook
@app.route('/webhook', methods=['POST'])
def handle_webhook():
    if request.headers.get('X-GitHub-Event') == 'push':
        payload = request.get_json()
        if payload and payload['ref'] == 'refs/heads/main':
            try:
                # 1. Change to project directory
                os.chdir('/home/dev/Letterboxd-Fans-Finder')

                # 2. Check if Git is installed
                which_git = subprocess.run(['which', 'git'], capture_output=True, text=True)
                if which_git.returncode != 0:
                    raise FileNotFoundError("Git is not installed or not in PATH")

                # 2. Pull the latest changes
                subprocess.run(['git', 'pull'], check=True)

                # 3. Activate virtual environment and install requirements
                activate_command = f". /home/dev/Letterboxd-Fans-Finder/venv/bin/activate && pip install -r requirements.txt"
                subprocess.run(activate_command, shell=True, executable='/bin/bash', check=True)

                # 4. Restart relevant services (adjust as needed)
                subprocess.run(['sudo', '-n', '/usr/bin/systemctl', 'restart', 'letterboxd-fans-finder'], check=True)
                subprocess.run(['sudo', '-n', '/usr/bin/systemctl', 'restart', 'letterboxd-fans-finder-worker'], check=True)

                return jsonify({'status': 'success'}), 200

            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                logging.error(f"Error during deployment: {e}")
                return jsonify({'status': 'error', 'message': str(e)}), 500

            except Exception as e:
                logging.error(f"Unexpected error: {e}")
                return jsonify({'status': 'error', 'message': 'An unexpected error occurred'}), 500
        else:
            return jsonify({'status': 'ignored'}), 200
    else:
        return jsonify({'status': 'ignored'}), 200

    
if __name__ == '__main__':
    app.run(debug=True)