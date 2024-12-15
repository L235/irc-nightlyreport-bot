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

# Configuration
BOUNCER_HOST = os.getenv('BOUNCER_HOST')
BOUNCER_PORT = int(os.getenv('BOUNCER_PORT', 6667))
NICKNAME = os.getenv('NICKNAME')
PASSWORD = os.getenv('PASSWORD', '')

MAILGUN_API_KEY = os.getenv('MAILGUN_API_KEY')
MAILGUN_DOMAIN = os.getenv('MAILGUN_DOMAIN')
TO_EMAIL = os.getenv('TO_EMAIL')
FROM_EMAIL = os.getenv('FROM_EMAIL')

LOG_DIR = os.getenv('LOG_DIR', 'logs')
SENT_LOGS_DIR = os.getenv('SENT_LOGS_DIR', 'sent_logs')

LAST_SENT_DAY_FILE = 'last_sent_day.txt'  # Stores the last processed day in YYYY-MM-DD format

class IRCBot:
    def __init__(self):
        self.client = irc.client.Reactor()
        self.connection = self.client.server()
        self.channels = set()
        self.nickname = NICKNAME
        self.setup_logging()
        self.ensure_directories()

    def setup_logging(self):
        logging.basicConfig(level=logging.INFO)

    def ensure_directories(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        os.makedirs(SENT_LOGS_DIR, exist_ok=True)

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

    def on_connect(self, connection, event):
        logging.info("Connected to IRC bouncer")
        # Retrieve current channels via WHOIS
        connection.whois([self.nickname])

    def on_whoischannels(self, connection, event):
        channels_str = event.arguments[1]
        channels = channels_str.split()
        channels = [chan.lstrip('@%+&~') for chan in channels]
        self.channels.update(channels)
        logging.info(f"Already connected to channels: {self.channels}")

        # After channels are known, start the day-checking thread
        # This thread will send yesterday's logs if not sent and then wait for midnight daily.
        self.start_day_check_thread()

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

    def start(self):
        self.client.process_forever()

    def start_day_check_thread(self):
        # First, attempt to send any missed days immediately (including yesterday if needed).
        self.send_missed_days_logs()

        # Then start a thread that waits until midnight and sends the previous day's logs daily.
        thread = threading.Thread(target=self.midnight_loop)
        thread.daemon = True
        thread.start()

    def midnight_loop(self):
        while True:
            # Calculate seconds until next midnight
            now = datetime.now()
            tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            seconds_until_midnight = (tomorrow - now).total_seconds()
            time.sleep(seconds_until_midnight)

            # It's now midnight, send yesterday's logs if not already sent
            self.send_missed_days_logs()

    def send_missed_days_logs(self):
        """
        Checks if there are any days between last_sent_day+1 and yesterday that haven't been processed,
        and sends their logs. If no logs for that day, we still mark the day as processed.
        """
        today = datetime.now().date()
        yesterday = today - timedelta(days=1)

        last_sent_day = self.get_last_sent_day()

        # If last_sent_day is None (no file), we haven't sent any day yet. Let's start from last_sent_day = day before yesterday
        if last_sent_day is None:
            # If no file, start from at least yesterday - 1 day to ensure we try to send yesterday first
            last_sent_day = yesterday - timedelta(days=1)

        # Now send logs for each day between last_sent_day+1 and yesterday inclusive
        day_to_send = last_sent_day + timedelta(days=1)
        while day_to_send <= yesterday:
            self.send_day_logs(day_to_send)
            # Update last_sent_day after processing
            self.set_last_sent_day(day_to_send)
            day_to_send += timedelta(days=1)

    def send_day_logs(self, day):
        """
        Send the logs for a specific day in YYYY-MM-DD format.
        If no logs are found, just mark as processed without sending.
        """
        day_str = day.strftime('%Y-%m-%d')
        logging.info(f"Preparing to send logs for {day_str}")

        # Find all log files for this specific day in LOG_DIR
        log_files = [
            f for f in os.listdir(LOG_DIR)
            if f.endswith(f"{day_str}.log") and os.path.isfile(os.path.join(LOG_DIR, f))
        ]

        if not log_files:
            logging.info(f"No logs found for {day_str}. Nothing to send.")
            return

        # Attach the logs
        attachments = []
        for filename in log_files:
            filepath = os.path.join(LOG_DIR, filename)
            with open(filepath, 'rb') as f:
                attachments.append(('attachment', (filename, f.read())))

        subject = f"IRC Logs for {day_str}"
        response = self.send_email(subject, f"Please find attached logs for {day_str}.", attachments)
        if response.status_code == 200:
            logging.info(f"Email sent successfully for {day_str}")
            # Move sent logs to SENT_LOGS_DIR
            for filename in log_files:
                src = os.path.join(LOG_DIR, filename)
                dst = os.path.join(SENT_LOGS_DIR, filename)
                os.rename(src, dst)
        else:
            logging.error(f"Failed to send email for {day_str}: {response.text}")

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
            }
        )

    def get_last_sent_day(self):
        if not os.path.isfile(LAST_SENT_DAY_FILE):
            return None
        with open(LAST_SENT_DAY_FILE, 'r') as f:
            day_str = f.read().strip()
            return datetime.strptime(day_str, '%Y-%m-%d').date()

    def set_last_sent_day(self, day):
        with open(LAST_SENT_DAY_FILE, 'w') as f:
            f.write(day.strftime('%Y-%m-%d'))

if __name__ == "__main__":
    bot = IRCBot()
    bot.connect()
    bot.start()
