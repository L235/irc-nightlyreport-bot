"""
IRC Logger Bot that connects to an IRC bouncer, logs channel messages, and emails daily logs.

This bot automatically:
- Connects to an IRC bouncer and joins previously configured channels
- Logs all public messages to daily log files per channel
- Emails the previous day's logs every day at midnight
- Handles missed days by sending logs for any days that were missed
- Moves sent logs to a separate directory for archival

Environment variables required:
    BOUNCER_HOST: IRC bouncer hostname
    BOUNCER_PORT: IRC bouncer port (default: 6667)
    NICKNAME: Bot's nickname
    PASSWORD: IRC bouncer password
    MAILGUN_API_KEY: Mailgun API key for sending emails
    MAILGUN_DOMAIN: Mailgun domain
    TO_EMAIL: Recipient email address
    FROM_EMAIL: Sender email address
    LOG_DIR: Directory for storing logs (default: 'logs')
    SENT_LOGS_DIR: Directory for archiving sent logs (default: 'sent_logs')
"""

import irc.client
import logging
import threading
import time
import requests
import os
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    """Configuration settings loaded from environment variables."""
    # IRC Settings
    BOUNCER_HOST = os.getenv('BOUNCER_HOST')
    BOUNCER_PORT = int(os.getenv('BOUNCER_PORT', 6667))
    NICKNAME = os.getenv('NICKNAME')
    PASSWORD = os.getenv('PASSWORD', '')

    # Email Settings
    MAILGUN_API_KEY = os.getenv('MAILGUN_API_KEY')
    MAILGUN_DOMAIN = os.getenv('MAILGUN_DOMAIN')
    TO_EMAIL = os.getenv('TO_EMAIL')
    FROM_EMAIL = os.getenv('FROM_EMAIL')

    # File System Settings
    LOG_DIR = os.getenv('LOG_DIR', 'logs')
    SENT_LOGS_DIR = os.getenv('SENT_LOGS_DIR', 'sent_logs')
    LAST_SENT_DAY_FILE = 'last_sent_day.txt'

class IRCEventHandler:
    """Handles IRC-specific events and connection management."""
    
    def strip_irc_color_codes(self, text):
        """
        Remove IRC formatting codes from text.
        
        Removes:
        - mIRC color codes (\x03) with optional foreground/background colors
        - Bold (\x02), underline (\x1F), reverse (\x16), and reset (\x0F) codes
        
        Args:
            text: The IRC message text to clean
            
        Returns:
            str: The text with all IRC formatting codes removed
        """
        # Remove mIRC color codes: \x03 followed by optional fg/bg color numbers
        text = re.sub(r'\x03(\d{1,2}(,\d{1,2})?)?', '', text)
        
        # Remove other formatting codes
        format_codes = ['\x02', '\x1F', '\x16', '\x0F']
        for code in format_codes:
            text = text.replace(code, '')
        
        return text

    def on_connect(self, connection, event):
        """
        Handle successful connection to the IRC bouncer.
        
        Queries the current channels via WHOIS command.
        """
        logging.info("Connected to IRC bouncer")
        connection.whois([self.nickname])

    def on_whoischannels(self, connection, event):
        """
        Handle WHOIS response to discover currently joined channels.
        
        Starts the day-checking thread after channels are known.
        """
        channels_str = event.arguments[1]
        channels = channels_str.split()
        channels = [chan.lstrip('@%+&~') for chan in channels]
        self.channels.update(channels)
        logging.info(f"Already connected to channels: {self.channels}")
        self.start_day_check_thread()

    def on_join(self, connection, event):
        """Handle channel join events by adding the channel to tracked channels."""
        channel = event.target
        self.channels.add(channel)
        logging.info(f"Joined channel {channel}")

    def on_pubmsg(self, connection, event):
        """
        Handle public messages by logging them to the appropriate channel log file.
        
        Strips IRC formatting codes before logging to ensure clean, readable logs.
        Format: [YYYY-MM-DD HH:MM:SS] <nickname> message
        """
        channel = event.target
        raw_message = event.arguments[0]
        message = self.strip_irc_color_codes(raw_message)
        nick = irc.client.NickMask(event.source).nick
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_line = f'[{timestamp}] <{nick}> {message}'
        logging.info(log_line)
        self.log_message(channel, log_line)

class LogManager:
    """Handles log file operations and email delivery."""
    
    def log_message(self, channel, message):
        """
        Write a message to the channel's log file for the current day.
        
        Args:
            channel: The channel name
            message: The formatted log message
        """
        date_str = datetime.now().strftime('%Y-%m-%d')
        safe_channel = channel.replace('#', '').replace('/', '_')
        filename = os.path.join(Config.LOG_DIR, f'{safe_channel}_{date_str}.log')
        with open(filename, 'a') as f:
            f.write(message + '\n')

    def send_email(self, subject, text, attachments):
        """
        Send an email using the Mailgun API.
        
        Args:
            subject: Email subject line
            text: Email body text
            attachments: List of file attachments
            
        Returns:
            requests.Response object from the Mailgun API call
        """
        logging.info("Sending email")
        return requests.post(
            f'https://api.mailgun.net/v3/{Config.MAILGUN_DOMAIN}/messages',
            auth=('api', Config.MAILGUN_API_KEY),
            files=attachments,
            data={
                'from': Config.FROM_EMAIL,
                'to': Config.TO_EMAIL,
                'subject': subject,
                'text': text
            }
        )

    def get_last_sent_day(self):
        """
        Get the last day for which logs were successfully sent.
        
        Returns:
            datetime.date object of the last sent day, or None if no days sent
        """
        if not os.path.isfile(Config.LAST_SENT_DAY_FILE):
            return None
        with open(Config.LAST_SENT_DAY_FILE, 'r') as f:
            day_str = f.read().strip()
            return datetime.strptime(day_str, '%Y-%m-%d').date()

    def set_last_sent_day(self, day):
        """
        Update the record of the last day for which logs were sent.
        
        Args:
            day: datetime.date object representing the day to record
        """
        with open(Config.LAST_SENT_DAY_FILE, 'w') as f:
            f.write(day.strftime('%Y-%m-%d'))

class IRCBot(IRCEventHandler, LogManager):
    """
    IRC bot that logs channel messages and emails them daily.
    
    The bot connects to an IRC bouncer, joins channels, logs messages to files,
    and sends daily email digests of the logs using Mailgun.
    """

    def __init__(self):
        """Initialize the IRC bot with basic configuration."""
        self.client = irc.client.Reactor()
        self.connection = self.client.server()
        self.channels = set()
        self.nickname = Config.NICKNAME
        self.setup_logging()
        self.ensure_directories()

    def setup_logging(self):
        """Configure basic logging for the bot."""
        logging.basicConfig(level=logging.INFO)

    def ensure_directories(self):
        """Create necessary directories for storing logs if they don't exist."""
        os.makedirs(Config.LOG_DIR, exist_ok=True)
        os.makedirs(Config.SENT_LOGS_DIR, exist_ok=True)

    def connect(self):
        """
        Connect to the IRC bouncer and set up event handlers.
        
        Raises:
            SystemExit: If connection fails
        """
        try:
            self.connection.connect(Config.BOUNCER_HOST, Config.BOUNCER_PORT, self.nickname, password=Config.PASSWORD)
        except irc.client.ServerConnectionError as e:
            logging.error(f"Failed to connect: {e}")
            raise SystemExit(1)
        self.connection.add_global_handler("welcome", self.on_connect)
        self.connection.add_global_handler("join", self.on_join)
        self.connection.add_global_handler("whoischannels", self.on_whoischannels)
        self.connection.add_global_handler("pubmsg", self.on_pubmsg)

    def start(self):
        """Start the bot's main event loop."""
        self.client.process_forever()

    def start_day_check_thread(self):
        """
        Start the background thread that handles daily log sending.
        
        First sends any missed days' logs, then starts a loop that
        waits until midnight to send the previous day's logs.
        """
        self.send_missed_days_logs()
        thread = threading.Thread(target=self.midnight_loop)
        thread.daemon = True
        thread.start()

    def midnight_loop(self):
        """
        Run an infinite loop that waits until midnight each day to send logs.
        
        Calculates the time until next midnight and sleeps until then.
        """
        while True:
            now = datetime.now()
            tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            seconds_until_midnight = (tomorrow - now).total_seconds()
            time.sleep(seconds_until_midnight)
            self.send_missed_days_logs()

    def send_missed_days_logs(self):
        """
        Process and send logs for any days that were missed.
        
        Checks the last sent day and sends logs for each day up to yesterday.
        If no logs exist for a day, that day is still marked as processed.
        """
        today = datetime.now().date()
        yesterday = today - timedelta(days=1)
        last_sent_day = self.get_last_sent_day()

        if last_sent_day is None:
            last_sent_day = yesterday - timedelta(days=1)

        day_to_send = last_sent_day + timedelta(days=1)
        while day_to_send <= yesterday:
            self.send_day_logs(day_to_send)
            self.set_last_sent_day(day_to_send)
            day_to_send += timedelta(days=1)

    def send_day_logs(self, day):
        """
        Send logs for a specific day via email.
        
        Args:
            day: datetime.date object representing the day to send logs for
            
        The logs are sent as email attachments using Mailgun.
        After successful sending, log files are moved to the sent logs directory.
        """
        day_str = day.strftime('%Y-%m-%d')
        logging.info(f"Preparing to send logs for {day_str}")

        log_files = [
            f for f in os.listdir(Config.LOG_DIR)
            if f.endswith(f"{day_str}.log") and os.path.isfile(os.path.join(Config.LOG_DIR, f))
        ]

        if not log_files:
            logging.info(f"No logs found for {day_str}. Nothing to send.")
            return

        attachments = []
        for filename in log_files:
            filepath = os.path.join(Config.LOG_DIR, filename)
            with open(filepath, 'rb') as f:
                attachments.append(('attachment', (filename, f.read())))

        subject = f"IRC Logs for {day_str}"
        response = self.send_email(subject, f"Please find attached logs for {day_str}.", attachments)
        if response.status_code == 200:
            logging.info(f"Email sent successfully for {day_str}")
            for filename in log_files:
                src = os.path.join(Config.LOG_DIR, filename)
                dst = os.path.join(Config.SENT_LOGS_DIR, filename)
                os.rename(src, dst)
        else:
            logging.error(f"Failed to send email for {day_str}: {response.text}")

if __name__ == "__main__":
    bot = IRCBot()
    bot.connect()
    bot.start()
