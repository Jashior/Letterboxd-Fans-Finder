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

                # 2. Pull the latest changes
                logging.info("Pulling latest changes...")
                subprocess.run(['git', 'pull'], check=True)

                # 3. Activate virtual environment and install requirements
                venv_dir = '/home/dev/Letterboxd-Fans-Finder/venv' 
                activate_command = f"source {venv_dir}/bin/activate && pip install -r requirements.txt"
                logging.info("Activating virtual environment and installing requirements...")
                subprocess.run(activate_command, shell=True, executable='/bin/bash', check=True)

                # 4. Restart relevant services (adjust as needed)
                for service_name in ['letterboxd-fans-finder', 'letterboxd-fans-finder-worker']:
                    logging.info(f"Restarting service: {service_name}")
                    subprocess.run(['sudo', '-n', '/usr/bin/systemctl', 'restart', service_name], check=True)

                return jsonify({'status': 'success'}), 200

            except subprocess.CalledProcessError as e:
                logging.error(f"Error during deployment: {e}")
                if e.returncode == 127:  # Command not found
                    return jsonify({'status': 'error', 'message': f"Command not found: {e.cmd}"}), 500
                elif e.returncode == 1: # General error from subprocess
                    # Log stderr output for more details if available
                    if e.stderr:
                        logging.error(f"Stderr output: {e.stderr}") 
                    return jsonify({'status': 'error', 'message': str(e)}), 500
                else:
                    return jsonify({'status': 'error', 'message': str(e)}), 500

            except FileNotFoundError as e:
                logging.error(f"File not found error: {e}")
                return jsonify({'status': 'error', 'message': str(e)}), 500

            except OSError as e:
                # Catch potential permission errors or other OS-related issues
                logging.error(f"OSError: {e}")
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