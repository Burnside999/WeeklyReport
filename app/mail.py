"""Reserved SMTP boundary. No network connection or delivery is implemented yet."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SMTPConfig:
    host: str
    port: int
    security: str
    sender: str
    password: str = field(repr=False)
    sender_name: str = ''
    recipient: str = ''
    recipient_name: str = ''

    @classmethod
    def from_settings(cls, settings):
        return cls(**{name:settings['smtp_'+name] for name in cls.__dataclass_fields__})


class SMTPMailer:
    def __init__(self, config: SMTPConfig):
        self.config = config

    async def send(self, *, subject: str, text: str, html: str | None = None):
        """Future delivery interface. Scheduling/content/recipient policy is not defined."""
        raise NotImplementedError('邮件发送尚未启用；当前仅保存配置并预留接口')
