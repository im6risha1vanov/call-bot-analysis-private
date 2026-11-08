UPDATE crm_invites SET password_enc = NULL WHERE password_enc IS NOT NULL;
