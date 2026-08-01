from django.core import signing


TICKET_SALT = "meritlense.live-call.v1"


def issue_socket_ticket(call, role):
    return signing.dumps({"call": str(call.public_id), "role": role}, salt=TICKET_SALT, compress=True)


def read_socket_ticket(ticket, max_age):
    return signing.loads(ticket, salt=TICKET_SALT, max_age=max_age)

