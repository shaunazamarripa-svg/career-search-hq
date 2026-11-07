"""Local operator commands. Never expose this file as an HTTP route."""
import argparse
import getpass
import os
import sqlite3
from pathlib import Path
from app import db,hash_password,now,DEFAULT_SETTINGS,audit,DB_PATH
import json

def create_user(email):
    email=email.strip().lower()
    password=getpass.getpass('Password (minimum 12 characters): ')
    if len(password)<12:raise SystemExit('Password too short')
    confirmation=getpass.getpass('Confirm password: ')
    if password!=confirmation:raise SystemExit('Passwords do not match')
    with db() as con:
        try:
            cur=con.execute('INSERT INTO users(email,password_hash,settings,created_at) VALUES(?,?,?,?)',(email,hash_password(password),json.dumps(DEFAULT_SETTINGS),now()))
        except sqlite3.IntegrityError:raise SystemExit('Account already exists')
        audit(con,cur.lastrowid,'operator_provisioned')
    print('Account created:',email)

def backup(destination):
    target=Path(destination).expanduser().resolve()
    if target.exists():raise SystemExit('Destination exists; refusing overwrite')
    target.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(DB_PATH) as source,sqlite3.connect(target) as dest:source.backup(dest)
    target.chmod(0o600)
    print('Backup created:',target)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('create-user');p.add_argument('email')
    p=sub.add_parser('backup');p.add_argument('destination')
    args=parser.parse_args()
    if args.command=='create-user':create_user(args.email)
    elif args.command=='backup':backup(args.destination)
