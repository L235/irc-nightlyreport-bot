# IRC Nightly Report Bot

A Python-based IRC bot that logs channel messages and sends daily email reports. The bot connects to an IRC bouncer, logs all public messages to daily log files per channel, and emails the previous day's logs every day at midnight.

## Features

- Automatic connection to IRC bouncer and channel management
- Daily log files for each channel
- Automatic email delivery of logs at midnight
- Handles missed days by sending logs for any skipped days
- Moves sent logs to a separate archive directory
- Uses Mailgun for reliable email delivery

## Requirements

- Python 3.6+
- IRC bouncer
- Mailgun account for email delivery

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/L235/irc-nightlyreport-bot.git
   cd irc-nightlyreport-bot
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Create a `.env` file with your configuration:
   ```env
   BOUNCER_HOST=your.bouncer.host
   BOUNCER_PORT=6667
   NICKNAME=your_bot_nickname
   PASSWORD=your_bouncer_password
   
   MAILGUN_API_KEY=your_mailgun_api_key
   MAILGUN_DOMAIN=your.mailgun.domain
   TO_EMAIL=recipient@example.com
   FROM_EMAIL=sender@your.mailgun.domain
   
   LOG_DIR=logs
   SENT_LOGS_DIR=sent_logs
   ```

## Usage

Run the bot:
```bash
python loggerbot.py
```

The bot will:
1. Connect to your IRC bouncer
2. Join previously configured channels
3. Log all public messages to daily files
4. Send yesterday's logs via email at midnight
5. Move sent logs to the archive directory

## Configuration

All configuration is done through environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `BOUNCER_HOST` | IRC bouncer hostname | Required |
| `BOUNCER_PORT` | IRC bouncer port | 6667 |
| `NICKNAME` | Bot's nickname | Required |
| `PASSWORD` | IRC bouncer password | "" |
| `MAILGUN_API_KEY` | Mailgun API key | Required |
| `MAILGUN_DOMAIN` | Mailgun domain | Required |
| `TO_EMAIL` | Recipient email address | Required |
| `FROM_EMAIL` | Sender email address | Required |
| `LOG_DIR` | Directory for storing logs | "logs" |
| `SENT_LOGS_DIR` | Directory for archiving sent logs | "sent_logs" |

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details. 