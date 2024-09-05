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
    

if __name__ == '__main__':
    app.run(debug=True)