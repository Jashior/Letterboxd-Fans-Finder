# Letterboxd Fans Finder

https://letterboxd.zanaris.dev/

![AuPwQXt](https://github.com/user-attachments/assets/b0a0c9b9-6296-4eac-9598-8287d8b0a578)

## Introduction
This Flask project allows you to find other Letterboxd users who share similar movie tastes based on your favorite films.

## Features

* Scrapes a user's favorite movies from Letterboxd.
* Finds other users who have liked combinations of those movies.
* Presents results in an organized way, showing users who share the most movies in common.


# Letterboxd Fans Finder: Deployment Guide

## Prerequisites
- A server with a public IP address
- Domain name configured to point to your server's IP
- Ubuntu 20.04 or later (adjust commands if using a different OS)
- Root access or a user with sudo privileges

## 1. Initial Server Setup

1.1. If not already done, create a non-root user with sudo privileges:
```bash
adduser letterboxd-gunicorn
usermod -aG sudo letterboxd-gunicorn
```

1.2. Switch to the new user:
```bash
su - letterboxd-gunicorn
```

## 2. System-wide Installations

Perform these steps outside of any virtual environment:

2.1. Update package lists:
```bash
sudo apt update
```

2.2. Install Nginx:
```bash
sudo apt install nginx
```

2.3. Install Redis:
```bash
sudo apt install redis-server
```

2.4. Install Python3 and venv (if not already installed):
```bash
sudo apt install python3 python3-venv
```

## 3. Application Setup

3.1. Clone the repository:
```bash
git clone https://github.com/Jashior/Letterboxd-Fans-Finder.git
cd Letterboxd-Fans-Finder
```

3.2. Set up a Python virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate
```

3.3. Install Python dependencies:
```bash
pip install -r requirements.txt
```

## 4. Nginx Setup

4.1. Verify Nginx is running:
```bash
sudo systemctl status nginx
```

4.2. Configure Nginx for your domain:
```bash
sudo nano /etc/nginx/sites-available/your_domain.conf
```

Add the following configuration (replace `<your_domain_name>` with your actual domain):
```nginx
server {
    listen 80;
    server_name <your_domain_name>;
    location / {
        proxy_pass http://unix:/home/dev/Letterboxd-Fans-Finder/letterboxd_app.sock;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

4.3. Create a symbolic link to enable the site:
```bash
sudo ln -s /etc/nginx/sites-available/your_domain.conf /etc/nginx/sites-enabled/
```

4.4. Test Nginx configuration:
```bash
sudo nginx -t
```

4.5. If the test is successful, reload Nginx:
```bash
sudo systemctl reload nginx
```

## 4. Systemd Service Setup

4.1. Create the Gunicorn service file:
```bash
sudo nano /etc/systemd/system/letterboxd-fans-finder.service
```

Add the following content:
```ini
[Unit]
Description=Gunicorn instance for Letterboxd Fans Finder
After=network.target

[Service]
User=letterboxd-gunicorn
Group=www-data
WorkingDirectory=/home/dev/Letterboxd-Fans-Finder
Environment="PATH=/home/dev/Letterboxd-Fans-Finder/venv/bin"
ExecStart=/home/dev/Letterboxd-Fans-Finder/venv/bin/gunicorn -c /home/dev/Letterboxd-Fans-Finder/gunicorn.conf.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

4.2. Create the worker service file:
```bash
sudo nano /etc/systemd/system/letterboxd-fans-finder-worker.service
```

Add the following content:
```ini
[Unit]
Description=Worker for Letterboxd Fans Finder
After=network.target

[Service]
User=letterboxd-gunicorn
Group=www-data
WorkingDirectory=/home/dev/Letterboxd-Fans-Finder
Environment="PATH=/home/dev/Letterboxd-Fans-Finder/venv/bin"
ExecStart=/home/dev/Letterboxd-Fans-Finder/venv/bin/python3 worker.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

4.3. Reload systemd manager configuration:
```bash
sudo systemctl daemon-reload
```

## 5. Redis Setup

5.1. Install Redis (if not already installed):
```bash
sudo apt install redis-server
```

5.2. Start and enable Redis:
```bash
sudo systemctl start redis-server
sudo systemctl enable redis-server
```

5.3. Verify Redis is running:
```bash
sudo systemctl status redis-server
```

## 6. Starting the Application

6.1. Start and enable the Gunicorn service:
```bash
sudo systemctl start letterboxd-fans-finder.service
sudo systemctl enable letterboxd-fans-finder.service
```

6.2. Start and enable the worker service:
```bash
sudo systemctl start letterboxd-fans-finder-worker.service
sudo systemctl enable letterboxd-fans-finder-worker.service
```

6.3. Verify both services are running:
```bash
sudo systemctl status letterboxd-fans-finder.service
sudo systemctl status letterboxd-fans-finder-worker.service
```

## 7. Final Steps

7.1. Set up SSL with Let's Encrypt (recommended for production):
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your_domain_name
```

7.2. Configure your firewall to allow HTTP (80), HTTPS (443), and SSH (22) traffic.

7.3. Test your application by visiting your domain in a web browser.

## Troubleshooting

- Check application logs: `sudo journalctl -u letterboxd-fans-finder.service`
- Check worker logs: `sudo journalctl -u letterboxd-fans-finder-worker.service`
- Check Nginx logs: `sudo tail -f /var/log/nginx/error.log`
- Check running services: `sudo systemctl list-units --type=service --state=running`
- Check status of services: `sudo systemctl status <service_name>` 
- Logs last 100 no pager: `sudo journalctl -u letterboxd-fans-finder.service -o cat --no-pager | tail -n 100`


Remember to keep your system and application updated regularly for security and performance improvements.

## 8. Pulling changes manually

8.1. If you encounter ownership issues on Ubuntu (where Git complains about "dubious ownership"), you can configure Git to trust the directory by running:

`git config --global --add safe.directory /home/dev/Letterboxd-Fans-Finder`

8.2. `git pull`

8.3. If the requirements.txt file has been updated (meaning new dependencies have been added or existing ones modified), install them within your virtual environment:

`pip install -r requirements.txt`

8.4. Finally, restart the Gunicorn service to apply the changes:

`sudo systemctl daemon-reload`
`sudo systemctl restart letterboxd-fans-finder.service`

## 9. Pulling changes and redploying automatically

9.1. Allow User to trigger restart of services

Add under the #User privilege section in /etc/sudoers. This configuration specifically grants the letterboxd-gunicorn user the ability to restart the letterboxd-fans-finder and letterboxd-fans-finder-worker services using systemctl without needing to provide a password. This allows the webhook script to successfully trigger service restarts upon code updates without encountering permission issues. 

`letterboxd-gunicorn ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart letterboxd-fans-finder, /usr/bin/systemctl restart letterboxd-fans-finder-worker`

9.2. Update `sudo nano /etc/nginx/sites-available/your_domain.conf`

```
server {
    listen 80;
    server_name <your_domain_name>;
    location / {
        proxy_pass http://unix:/home/dev/Letterboxd-Fans-Finder/letterboxd_app.sock;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location /webhook {
        proxy_pass http://unix:/home/dev/Letterboxd-Fans-Finder/letterboxd_app.sock;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Content-Type application/json;
    }
}
```

9.3. Enable webhooks on github project

Payload URL: `<server ip>/webhook`
Content type: `application/json`

Test

`~$ curl -X POST -H "Content-Type: application/json" -H "X-GitHub-Event: push" -d '{"ref": "refs/heads/main"}' <sever ip>/webhook`

Example response:

`{"message":"Command '['/usr/bin/sudo', '/usr/bin/systemctl', 'restart', 'letterboxd-fans-finder']' died with <Signals.SIGTERM: 15>.","status":"error"}`


