"""Signals the messaging app sends so other apps can react without messaging importing them."""

from django.dispatch import Signal

# Sent for every new message a person (not the account) wrote. Keyword arguments: conversation, message.
customer_message_received = Signal()
