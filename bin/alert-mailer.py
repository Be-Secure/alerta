#!/usr/bin/env python
########################################
#
# alert-mailer.py - Alerter Mailer module
#
########################################

import os
import sys
import time
import threading
import urllib2
try:
    import json
except ImportError:
    import simplejson as json
import smtplib
from email.MIMEMultipart import MIMEMultipart
from email.MIMEText import MIMEText
from email.MIMEImage import MIMEImage
import stomp
import datetime
import logging
import uuid
import re

__version__ = "1.0"

BROKER_LIST  = [('devmonsvr01',61613), ('localhost', 61613)] # list of brokers for failover
NOTIFY_TOPIC = '/topic/notify'
SMTP_SERVER  = 'mx.gudev.gnl:25'
ALERTER_MAIL = 'alerta@guardian.co.uk'
MAILING_LIST = ['nick.satterly@guardian.co.uk', 'simon.huggins@guardian.co.uk']

LOGFILE = '/var/log/alerta/alert-mailer.log'
PIDFILE = '/var/run/alert-mailer.pid'

_TokenThread = None            # Worker thread object
_Lock = threading.Lock()       # Synchronization lock
_token_rate = 30               # Add a token every 30 seconds
tokens = 20

class MessageHandler(object):
    def on_error(self, headers, body):
        logging.error('Received an error %s', body)

    def on_message(self, headers, body):
        global tokens

        logging.debug("Received alert; %s", body)

        alert = dict()
        alert = json.loads(body)

        logging.info('%s %s %s %s', alert['uuid'], alert['source'], alert['event'], alert['severity'])

        if tokens:
            _Lock.acquire()
            tokens -= 1
            _Lock.release()
            logging.debug('Taken a token, there are only %d left', tokens)
        else:
            # if there are no tokens don't send this alert (wait until next time)
            logging.info('No tokens left, rate limiting this alert')
            return
   
        if 'previousSeverity' in alert:
            prev = alert['previousSeverity']
        else:
            prev = '(unknown)'
        logging.info('%s %s %s %s -> %s', alert['uuid'], alert['source'], alert['event'], prev, alert['severity'])

        text = ''
        text += '%s\n' % (alert['summary'])
        text += 'Alert Details\n'
        text += 'Alert ID: %s\n' % (alert['uuid'])
        text += 'Create Time: %s\n' % (alert['createTime'])
        text += 'Source: %s\n' % (alert['source'])
        text += 'Environment: %s\n' % (alert['environment'])
        text += 'Service/Grid: %s\n' % (alert['service'])
        text += 'Event Name: %s\n' % (alert['event'])
        text += 'Event Group: %s\n' % (alert['group'])
        text += 'Event Value: %s\n' % (alert['value'])
        if 'previousSeverity' in alert:
            text += 'State: %s -> %s\n' % (alert['previousSeverity'], alert['severity'])
        else:
            text += 'State: %s -> %s\n' % ('(unknown)', alert['severity'])
        text += 'Text: %s\n' % (alert['text'])
        if 'alertRule' in alert:
            text += 'Alert Rule: %s\n' % (alert['alertRule'])
        if 'duplicateCount' in alert:
            text += 'Duplicate Count: %s\n' % (alert['duplicateCount'])
        if 'moreInfo' in alert:
            text += 'More Info: %s\n' % (alert['moreInfo'])
        text += 'Tokens: %d left\n' % (tokens)
        text += 'Historical Data\n'
        if 'graphs' in alert:
            for g in alert['graphs']:
                text += '%s\n' % (g)
        text += 'Raw Alert\n'
        text += '%s\n' % (json.dumps(alert))
        text += 'Generated by %s on %s at %s\n' % ('alert-mailer.py', os.uname()[1], datetime.datetime.now().strftime("%a %d %b %H:%M:%S"))

        logging.debug('Raw Text: %s', text)
   
        html = '<p><table border="0" cellpadding="0" cellspacing="0" width="100%">\n'  # table used to center email
        html += '<tr><td bgcolor="#ffffff" align="center">\n'
        html += '<table border="0" cellpadding="0" cellspacing="0" width="700">\n'     # table used to set width of email
        html += '<tr><td bgcolor="#425470"><p align="center" style="font-size:24px;color:#d9fffd;font-weight:bold;"><strong>%s</strong></p>\n' % (alert['summary'])

        html += '<tr><td><p align="left" style="font-size:18px;line-height:22px;color:#c25130;font-weight:bold;">Alert Details</p>\n'
        html += '<table>\n'
        html += '<tr><td><b>Alert ID:</b></td><td>%s</td></tr>\n' % (alert['uuid'])
        html += '<tr><td><b>Create Time:</b></td><td>%s</td></tr>\n' % (alert['createTime'])
        html += '<tr><td><b>Source:</b></td><td>%s</td></tr>\n' % (alert['source'])
        html += '<tr><td><b>Environment:</b></td><td>%s</td></tr>\n' % (alert['environment'])
        html += '<tr><td><b>Service/Grid:</b></td><td>%s</td></tr>\n' % (alert['service'])
        html += '<tr><td><b>Event Name:</b></td><td>%s</td></tr>\n' % (alert['event'])
        html += '<tr><td><b>Event Group:</b></td><td>%s</td></tr>\n' % (alert['group'])
        html += '<tr><td><b>Event Value:</b></td><td>%s</td></tr>\n' % (alert['value'])
        if 'previousSeverity' in alert:
            html += '<tr><td><b>State:</b></td><td>%s -> %s</td></tr>\n' % (alert['previousSeverity'], alert['severity'])
        else:
            html += '<tr><td><b>State:</b></td><td>%s -> %s</td></tr>\n' % ('(unknown)', alert['severity'])
        html += '<tr><td><b>Text:</b></td><td>%s</td></tr>\n' % (alert['text'])
        if 'alertRule' in alert:
            html += '<tr><td><b>Alert Rule:</b></td><td>%s</td></tr>\n' % (alert['alertRule'])
        if 'duplicateCount' in alert:
            html += '<tr><td><b>Duplicate Count:</b></td><td>%s</td></tr>\n' % (alert['duplicateCount'])
        if 'moreInfo' in alert:
            html += '<tr><td><b>More Info:</b></td><td><a href="%s">ganglia</a></td></tr>\n' % (alert['moreInfo'])
        html += '<tr><td><b>Tokens:</b></td><td>%d left</td></tr>\n' % (tokens)
        html += '</table>\n'
        html += '</td></tr>\n'
        html += '<tr><td><p align="left" style="font-size:18px;line-height:22px;color:#c25130;font-weight:bold;">Historical Data</p>\n'
        if 'graphs' in alert:
            graph_cid = dict()
            for g in alert['graphs']:
                graph_cid[g] = str(uuid.uuid4())
                html += '<tr><td><img src="cid:'+graph_cid[g]+'"></td></tr>\n'
        html += '<tr><td><p align="left" style="font-size:18px;line-height:22px;color:#c25130;font-weight:bold;">Raw Alert</p>\n'
        html += '<tr><td><p align="left" style="font-family: \'Courier New\', Courier, monospace">%s</p></td></tr>\n' % (json.dumps(alert))
        html += '<tr><td>Generated by %s on %s at %s</td></tr>\n' % ('alert-mailer.py', os.uname()[1], datetime.datetime.now().strftime("%a %d %b %H:%M:%S"))
        html += '</table>'
        html += '</td></tr></table>'
        html += '</td></tr></table>'

        logging.debug('HTML Text %s', html)
        
        msg_root = MIMEMultipart('related')
        msg_root['Subject'] = alert['summary']
        msg_root['From']    = ALERTER_MAIL
        msg_root['To']      = ','.join(MAILING_LIST)
        msg_root.preamble   = 'This is a multi-part message in MIME format.'
        
        msg_alt = MIMEMultipart('alternative')
        msg_root.attach(msg_alt)
        
        msg_text = MIMEText(text, 'plain')
        msg_alt.attach(msg_text)
        
        msg_html = MIMEText(html, 'html')
        msg_alt.attach(msg_html)
   
        if 'graphs' in alert:
            msg_img = dict() 
            for g in alert['graphs']:
                try:
                    image = urllib2.urlopen(g).read()
                    msg_img[g] = MIMEImage(image)
                    logging.debug('graph cid %s', graph_cid[g])
                    msg_img[g].add_header('Content-ID', '<'+graph_cid[g]+'>')
                    msg_root.attach(msg_img[g])
                except:
                    pass
        
        try:
            logging.info('%s Send via email  %s', alert['uuid'], alert['summary'])
            s = smtplib.SMTP('mx.gudev.gnl', 25)
            s.sendmail(ALERTER_MAIL, MAILING_LIST, msg_root.as_string())
            s.quit
        except Exception, e:
            logging.error('Sendmail failed %s %s', e, alert['summary'])

    def on_disconnected(self):
        global conn

        logging.warning('Connection lost. Attempting auto-reconnect to %s', NOTIFY_TOPIC)
        conn.start()
        conn.connect(wait=True)
        conn.subscribe(destination=NOTIFY_TOPIC, ack='auto', headers={'selector': "repeat = 'false'"})

class TokenTopUp(threading.Thread):

    def __init__(self):
        threading.Thread.__init__(self)
        self.running      = False
        self.shuttingdown = False

    def shutdown(self):
        self.shuttingdown = True
        if not self.running:
            return
        self.join()

    def run(self):
        global _token_rate, tokens
        self.running = True

        while not self.shuttingdown:
            if self.shuttingdown:
                break

            if tokens < 20:
                _Lock.acquire()
                tokens += 1
                _Lock.release()
 
            if not self.shuttingdown:
                logging.debug('Added token to bucket. There are now %d tokens', tokens)
                time.sleep(_token_rate)

        self.running = False

def main():
    global conn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s alert-mailer[%(process)d] %(levelname)s - %(message)s", filename=LOGFILE)
    logging.info('Starting up Alert Mailer version %s', __version__)

    # Write pid file
    if os.path.isfile(PIDFILE):
        logging.error('%s already exists, exiting' % PIDFILE)
        sys.exit()
    else:
        file(PIDFILE, 'w').write(str(os.getpid()))

    # Connect to message broker
    try:
        conn = stomp.Connection(BROKER_LIST)
        conn.set_listener('', MessageHandler())
        conn.start()
        conn.connect(wait=True)
        conn.subscribe(destination=NOTIFY_TOPIC, ack='auto', headers={'selector': "repeat = 'false'"})
    except Exception, e:
        logging.error('Stomp connection error: %s', e)

    # Start token bucket thread
    logging.info('Start token bucket rate limiting thread')
    _TokenThread = TokenTopUp()
    _TokenThread.start()

    while True:
        try:
            time.sleep(0.01)
        except KeyboardInterrupt, SystemExit:
            conn.disconnect()
            _TokenThread.shutdown()
            os.unlink(PIDFILE)
            sys.exit()

if __name__ == '__main__':
    main()
