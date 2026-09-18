"""SMTP delivery. Recipients belong to templates, never to global settings."""
import asyncio
import re
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid


def email_address(value):
    if not isinstance(value, str) or len(value) > 254 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", value):
        raise ValueError('请填写有效的邮箱地址，不含显示名称或空格')
    local, domain = value.rsplit('@', 1)
    if len(local) > 64 or local.startswith('.') or local.endswith('.') or '..' in local or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label) for label in domain.split('.')):
        raise ValueError('邮箱地址格式错误')
    return value


def recipients_list(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 5:
        raise ValueError('每个模板须填写 1–5 个接收邮箱')
    result = [email_address(v) for v in value]
    if len({x.lower() for x in result}) != len(result):
        raise ValueError('接收邮箱不能重复')
    return result


class DeliveryError(Exception):
    """Safe public error; raw SMTP exceptions can contain private messages."""


@dataclass(frozen=True)
class SMTPConfig:
    host: str
    port: int
    security: str
    sender: str
    password: str = field(repr=False)
    sender_name: str = ''

    @classmethod
    def from_settings(cls, settings):
        return cls(**{name: settings['smtp_' + name] for name in cls.__dataclass_fields__})


class SMTPMailer:
    def __init__(self, config: SMTPConfig):
        self.config = config

    async def send(self, *, subject: str, text: str, recipients: list[str]):
        recipients = recipients_list(recipients)
        c = self.config
        if not c.host or not c.password or c.security not in ('ssl', 'starttls'):
            raise DeliveryError('请先在设置中配置 SMTP 服务器、加密方式和发送密码')
        email_address(c.sender)
        if any(ch in subject + c.sender_name for ch in '\r\n'):
            raise ValueError('邮件标题和发送名称不能包含换行')
        message = EmailMessage()
        message['Subject'] = subject
        message['From'] = formataddr((c.sender_name, c.sender))
        message['To'] = ', '.join(recipients)
        message['Date'] = formatdate(localtime=False)
        message['Message-ID'] = make_msgid()
        message.set_content(text)
        await asyncio.to_thread(self._deliver, message, recipients)

    def _deliver(self, message, recipients):
        c, smtp = self.config, None
        try:
            context = ssl.create_default_context()
            if c.security == 'ssl':
                smtp = smtplib.SMTP_SSL(c.host, c.port, timeout=30, context=context)
            else:
                smtp = smtplib.SMTP(c.host, c.port, timeout=30)
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
            smtp.login(c.sender, c.password)
            refused = smtp.send_message(message, from_addr=c.sender, to_addrs=recipients)
            if refused:
                # Some recipients may have received it. Never automatically retry.
                raise DeliveryError('部分收件人被拒收，其他收件人可能已收到；请核实后再重置或重发')
        except DeliveryError:
            raise
        except smtplib.SMTPAuthenticationError:
            raise DeliveryError('SMTP 登录失败，请检查发送邮箱和授权码') from None
        except smtplib.SMTPRecipientsRefused:
            raise DeliveryError('所有收件人均被 SMTP 服务器拒收，请检查邮箱地址') from None
        except (smtplib.SMTPException, OSError):
            raise DeliveryError('SMTP 发送失败或结果不确定，请核实邮箱后再重置或重发') from None
        finally:
            if smtp is not None:
                # DATA acceptance is the success boundary, not QUIT.
                try:
                    smtp.quit()
                except (smtplib.SMTPException, OSError):
                    smtp.close()
