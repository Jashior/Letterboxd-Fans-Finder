from flask import Flask, jsonify
from rq import Queue
from worker import conn 
from tasks import scrape_letterboxd_favorites, scrape_letterboxd_fans, get_all_fans_of_favorites

app = Flask(__name__)
q = Queue(connection=conn)

# Route to get all fans for a user's favorites, including combinations
@app.route('/all_fans/<username>')
def get_all_fans(username):
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

# Route to retrieve results of a job (favorites, fans, or combined fan data)
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