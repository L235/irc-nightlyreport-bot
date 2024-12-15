import irc.client
import logging
import threading
import time
import requests
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configuration (loaded from environment variables)
BOUNCER_HOST = os.getenv('BOUNCER_HOST')  # IRC bouncer host
BOUNCER_PORT = int(os.getenv('BOUNCER_PORT', 6667))  # IRC bouncer port
NICKNAME = os.getenv('NICKNAME')  # Bot nickname
PASSWORD = os.getenv('PASSWORD')  # IRC bouncer password (if any)

MAILGUN_API_KEY = os.getenv('MAILGUN_API_KEY')  # Mailgun API key
MAILGUN_DOMAIN = os.getenv('MAILGUN_DOMAIN')  # Mailgun domain
TO_EMAIL = os.getenv('TO_EMAIL')  # Recipient's email address
FROM_EMAIL = os.getenv('FROM_EMAIL')  # Sender's email address
EMAIL_INTERVAL_DAYS = int(os.getenv('EMAIL_INTERVAL_DAYS', 1))  # Email interval in days (default 1 day)

LOG_DIR = os.getenv('LOG_DIR', 'logs')  # Directory where logs will be stored
SENT_LOGS_DIR = os.getenv('SENT_LOGS_DIR', 'sent_logs')  # Directory where sent logs will be moved

class IRCBot:
    def __init__(self):
        self.client = irc.client.Reactor()
        self.connection = self.client.server()
        self.channels = set()
        self.nickname = NICKNAME  # Store the nickname
        self.setup_logging()
        self.schedule_next_email()

    def setup_logging(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        os.makedirs(SENT_LOGS_DIR, exist_ok=True)
        logging.basicConfig(level=logging.INFO)

    def on_connect(self, connection, event):
        logging.info("Connected to IRC bouncer")
        # Send WHOIS for our own nickname to get the list of channels
        connection.whois([self.nickname])

    def on_whoischannels(self, connection, event):
        # event.arguments[1] contains the list of channels
        channels_str = event.arguments[1]
        channels = channels_str.split()
        # Remove channel prefixes like '@', '+', etc.
        channels = [chan.lstrip('@%+&~') for chan in channels]
        self.channels.update(channels)
        logging.info(f"Already connected to channels: {self.channels}")
        # Start email sending thread after channels are loaded
        self.start_email_thread()

    def on_join(self, connection, event):
        channel = event.target
        self.channels.add(channel)
        logging.info(f"Joined channel {channel}")

    def on_pubmsg(self, connection, event):
        channel = event.target
        message = event.arguments[0]
        nick = irc.client.NickMask(event.source).nick
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_line = f'[{timestamp}] <{nick}> {message}'
        logging.info(log_line)
        self.log_message(channel, log_line)

    def log_message(self, channel, message):
        date_str = datetime.now().strftime('%Y-%m-%d')
        safe_channel = channel.replace('#', '').replace('/', '_')
        filename = os.path.join(LOG_DIR, f'{safe_channel}_{date_str}.log')
        with open(filename, 'a') as f:
            f.write(message + '\n')

    def connect(self):
        try:
            self.connection.connect(BOUNCER_HOST, BOUNCER_PORT, self.nickname, password=PASSWORD)
        except irc.client.ServerConnectionError as e:
            logging.error(f"Failed to connect: {e}")
            raise SystemExit(1)
        self.connection.add_global_handler("welcome", self.on_connect)
        self.connection.add_global_handler("join", self.on_join)
        self.connection.add_global_handler("whoischannels", self.on_whoischannels)
        self.connection.add_global_handler("pubmsg", self.on_pubmsg)

    def start(self):
        # Start IRC client
        self.client.process_forever()

    def start_email_thread(self):
        # Start email sending thread after channels are known
        email_thread = threading.Thread(target=self.email_logs_periodically)
        email_thread.daemon = True
        email_thread.start()

    def schedule_next_email(self):
        # Determine the next scheduled email time
        now = datetime.now()
        self.next_email_time_file = 'next_email_time.txt'
        if os.path.isfile(self.next_email_time_file):
            with open(self.next_email_time_file, 'r') as f:
                next_send_str = f.read().strip()
                next_send_time = datetime.strptime(next_send_str, '%Y-%m-%d %H:%M:%S')
        else:
            next_send_time = now
        if next_send_time <= now:
            # It's time to send emails now
            self.next_email_time = now
        else:
            self.next_email_time = next_send_time

    def email_logs_periodically(self):
        while True:
            now = datetime.now()
            sleep_time = (self.next_email_time - now).total_seconds()
            if sleep_time > 0:
                time.sleep(sleep_time)
            self.send_email_with_logs()
            # Schedule next email
            self.next_email_time = self.next_email_time + timedelta(days=EMAIL_INTERVAL_DAYS)
            with open(self.next_email_time_file, 'w') as f:
                f.write(self.next_email_time.strftime('%Y-%m-%d %H:%M:%S'))

    def send_email_with_logs(self):
        logging.info("Preparing to send email with logs")
        # Collect logs that haven't been sent yet (logs in LOG_DIR)
        unsent_logs = []
        log_files = [f for f in os.listdir(LOG_DIR) if os.path.isfile(os.path.join(LOG_DIR, f))]
        for filename in log_files:
            filepath = os.path.join(LOG_DIR, filename)
            with open(filepath, 'rb') as f:
                unsent_logs.append(('attachment', (filename, f.read())))
        if unsent_logs:
            subject = f"IRC Logs up to {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            response = self.send_email(subject, "Please find attached logs.", unsent_logs)
            if response.status_code == 200:
                logging.info("Email sent successfully")
                # Move sent logs to SENT_LOGS_DIR
                for filename in log_files:
                    src = os.path.join(LOG_DIR, filename)
                    dst = os.path.join(SENT_LOGS_DIR, filename)
                    os.rename(src, dst)
            else:
                logging.error(f"Failed to send email: {response.text}")
        else:
            logging.info("No new logs to send")

    def send_email(self, subject, text, attachments):
        logging.info("Sending email")
        return requests.post(
            f'https://api.mailgun.net/v3/{MAILGUN_DOMAIN}/messages',
            auth=('api', MAILGUN_API_KEY),
            files=attachments,
            data={
                'from': FROM_EMAIL,
                'to': TO_EMAIL,
                'subject': subject,
                'text': text
            })

if __name__ == "__main__":
    bot = IRCBot()
    bot.connect()
    bot.start()
