#!/usr/bin/env python3

import argparse
import configparser
import getpass
import shutil
import subprocess
import sys
import re
import os.path

import mysql.connector


def query_user(prompt, var_type, default=None, hide=False):
    """
    Query user for a certain value and let them try again, if they fail entering a valid value.

    A default value can be given, that is returned when the user enters an empty string. If no default value is given,
    an empty input may still be valid if the given type allows construction from empty string. The default value – if
    given – is automatically appended to the prompt (in brackets).

    Booleans must be entered with 'y' or 'n' by the user.

    :param prompt: The prompt string to be presented to the user
    :type prompt: str
    :param var_type: The type to convert the value to. If it fails with ValueError the user is queried again.
    :type var_type: type
    :param default: The default value. Must be of the var_type or None.
    :param hide: If True, getpass() is used for the query to hide the input in the terminal (for password inputs etc.)
    :type hide: bool
    :return: The entered value, converted to the given var_type
    :rtype: var_type
    """
    if var_type is bool:
        prompt += " [{}/{}]".format('Y' if default else 'y', 'n' if default or default is None else 'N')
    elif var_type is str:
        if default:
            prompt += " [{}]".format(default)
    else:
        prompt += " [{}]".format(default)

    while True:
        result = getpass.getpass(prompt + " ") if hide else input(prompt + " ")
        if not result and default is not None:
            return default
        if var_type is bool:
            if result.lower() in "yn":
                return result.lower() == 'y'
            else:
                print("Invalid input. Must be 'y' or 'n'.")
        else:
            try:
                return var_type(result)
            except ValueError:
                print("Not a valid {}. Please try again.".format(var_type.__name__))


def hash_pw(password):
    """
    Hash the given plain password with Dovecot's SHA512-CRYPT hashing function. The result can be stored to the accounts
    Database to check

    :param password: The plain password
    :type password: str
    :return: The password hash
    :rtype: str
    """
    result = subprocess.run(['doveadm', 'pw', '-s', 'SHA512-CRYPT'], stdout=subprocess.PIPE,
                            input="{0}\n{0}\n".format(password), universal_newlines=True)
    result.check_returncode()
    return result.stdout.strip()


def delete_mailbox(domain, user):
    """
    Delete the dovecot mailbox located at /var/vmail/mailboxes/<domain>/<user>/.

    :param domain: The domain
    :param user: The user
    """
    shutil.rmtree(os.path.join('/var/vmail/mailboxes', domain, user))


def list_accounts(db, _):
    cursor = db.cursor(named_tuple=True)
    cursor.execute("SELECT `username`, `domain`, NULL AS `target_username`, NULL AS `target_domain`, `enabled`, "
                   "`sendonly` "
                   "FROM `accounts` "
                   "UNION SELECT `source_username`, `source_domain`, `destination_username`, `destination_domain`, "
                   "`enabled`, NULL "
                   "FROM `aliases`"
                   "ORDER BY `domain`, `username`")
    for account in cursor:
        print("{}{:>15}@{}{}{}".format("[dis] " if not account.enabled else "      ",
                                       account.username, account.domain,
                                       "\t→ {}@{}".format(account.target_username, account.target_domain)
                                       if account.target_username else "",
                                       "\t[send-only]" if account.sendonly else ""))
    cursor.close()
    return 0


def add_account(db, account_name):
    cursor = db.cursor(named_tuple=True)

    # Check if name is already an account or alias
    user, domain = account_name.split('@')
    cursor.execute("SELECT COUNT(*) AS c FROM `accounts` WHERE `username` = %s AND `domain` = %s",
                   (user, domain))
    if cursor.fetchone().c > 0:
        cursor.close()
        print("The account {} exists already.".format(account_name))
        return 2
    cursor.execute("SELECT `destination_username`, `destination_domain` "
                   "FROM `aliases` "
                   "WHERE `source_username` = %s AND `source_domain` = %s",
                   (user, domain))
    current_alias = cursor.fetchone()
    if current_alias:
        print("Warning: This address is currently an alias of {}@{}.".format(current_alias.destination_username,
                                                                             current_alias.destination_domain))
        if not query_user("Do you still want to create an account at the address?", bool, False):
            cursor.close()
            return 0

    # Check if domain exists
    cursor.execute("SELECT COUNT(*) AS c FROM `domains` WHERE `domain` = %s",
                   (domain,))

    if cursor.fetchone().c != 1:
        cursor.close()
        print("The domain {} is not registered as virtual mail domain yet. Please add it manually to the database"
              .format(domain))
        return 2

    # Ask user for information
    pass1 = query_user("New account's password:", str, hide=True)
    if not pass1:
        print("Password must not be empty.")
        cursor.close()
        return 64
    pass2 = query_user("Type password again:", str, hide=True)
    if pass1 != pass2:
        print("Passwords do not match.")
        cursor.close()
        return 64
    enabled = query_user("Enable Account?", bool, True)
    send_only = query_user("Create send-only account?", bool, False)
    if not send_only:
        quota = query_user("Storage quota in MB:", int, 128)
    else:
        quota = 0

    # Hash password and create account
    pass_hash = hash_pw(pass1)
    cursor.execute("INSERT INTO `accounts` (`username`, `domain`, `password`, `quota`, `enabled`, `sendonly`) "
                   "VALUES(%s,%s,%s,%s,%s,%s)",
                   (user, domain, pass_hash, quota, enabled, send_only))
    cursor.close()
    db.commit()
    return 0


def change_account(db, account_name):
    cursor = db.cursor(named_tuple=True)

    # Get current settings and exit if accounts doesn't exist
    user, domain = account_name.split('@')
    cursor.execute("SELECT `id`, `username`, `domain`, `enabled`, `quota`, `sendonly` "
                   "FROM `accounts` "
                   "WHERE `username` = %s AND `domain` = %s",
                   (user, domain))
    current_account = cursor.fetchone()
    if not current_account:
        cursor.fetchall()
        cursor.close()
        print("This account does not exist yet.")
        return 2

    # Query user for new values
    enabled = query_user("Account enabled?", bool, bool(current_account.enabled))
    send_only = query_user("Send-only account?", bool, bool(current_account.sendonly))
    if not send_only:
        quota = query_user("Quota in MB:", int, current_account.quota)
    else:
        quota = 0

    # Store new values
    cursor.execute("UPDATE `accounts` SET `enabled` = %s, `quota` = %s, `sendonly` = %s WHERE `id` = %s",
                   (enabled, quota, send_only, current_account.id))
    if cursor.rowcount != 1:
        print("Error: {} rows have been effected by database query.".format(cursor.rowcount))
        return 2
    print("Stored new values.")
    cursor.close()
    db.commit()

    # Ask user, if mailbox shall be deleted
    if not send_only and current_account.sendonly:
        if query_user("Do you want to delete the user's mailbox?", bool, False):
            if query_user("really?", bool, False):
                delete_mailbox(domain, user)

    return 0


def change_password(db, account_name):
    cursor = db.cursor(named_tuple=True)

    # Get id and exit if accounts doesn't exist
    user, domain = account_name.split('@')
    cursor.execute("SELECT `id` FROM `accounts` WHERE `username` = %s AND `domain` = %s",
                   (user, domain))
    current_account = cursor.fetchone()
    if not current_account:
        cursor.fetchall()
        cursor.close()
        print("This account does not exist.")
        return 2

    # Query user for new password
    pass1 = query_user("New account's password:", str, hide=True)
    if not pass1:
        print("Password must not be empty.")
        cursor.close()
        return 64
    pass2 = query_user("Type password again:", str, hide=True)
    if pass1 != pass2:
        print("Passwords do not match.")
        cursor.close()
        return 64

    # Hash password and create account
    pass_hash = hash_pw(pass1)

    # Hash password and store new hash
    cursor.execute("UPDATE `accounts` SET `password` = %s WHERE `id` = %s",
                   (pass_hash, current_account.id))
    if cursor.rowcount != 1:
        print("Error: {} rows have been effected by database query.".format(cursor.rowcount))
        return 2
    print("Stored new password.")
    cursor.close()
    db.commit()


def delete_account(db, account_name):
    cursor = db.cursor(named_tuple=True)

    # Get id and exit if accounts doesn't exist
    user, domain = account_name.split('@')
    cursor.execute("SELECT `id` FROM `accounts` WHERE `username` = %s AND `domain` = %s",
                   (user, domain))
    current_account = cursor.fetchone()
    if not current_account:
        cursor.fetchall()
        cursor.close()
        print("This account does not exist.")
        return 2

    if not query_user("Do you really want to delete the account {}?".format(account_name), bool, False):
        cursor.close()
        return 0

    # Delete database entry
    cursor.execute("DELETE FROM `accounts` WHERE `id` = %s", (current_account.id,))
    if cursor.rowcount != 1:
        print("Error: {} rows have been effected by database query.".format(cursor.rowcount))
        return 2
    print("Account has been deleted.")
    cursor.close()
    db.commit()

    # Ask user, if mailbox shall be deleted
    if query_user("Do you want to delete the user's mailbox?", bool, False):
        delete_mailbox(domain, user)

    return 0


def add_alias(db, alias_name):
    # TODO
    print("Not implemented yet.")


def change_alias(db, alias_name):
    # TODO
    print("Not implemented yet.")


def delete_alias(db, alias_name):
    # TODO
    print("Not implemented yet.")


# Map cli commands to handler functions
COMMANDS = {
    'list': list_accounts,
    'add': add_account,
    'change': change_account,
    'pw': change_password,
    'delete': delete_account,
    'addalias': add_alias,
    'changealias': change_alias,
    'deletealias': delete_alias
}
# Commands that do not require a mail address
SIMPLE_COMMANDS = ['list']


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A python cli interface to make modifications to accounts in the vmail"
                                                 " MySQL database")
    parser.add_argument('-c', '--config', type=str, default="/root/managevmail/config.ini",
                        help="The path to the config.ini file, containing database options.")
    parser.add_argument('command', type=str, help="The main command. Must be one of {}"
                                                  .format(", ".join(COMMANDS.keys())))
    parser.add_argument('address', type=str, help="The email address (account or alias) to be added/modified/deleted.",
                        default="", nargs='?')
    args = parser.parse_args()

    if args.command not in COMMANDS:
        print("{} is not a valid command. Please use one of: {}".format(args.command, ", ".join(COMMANDS.keys())))
        sys.exit(64)

    if args.command not in SIMPLE_COMMANDS:
        if not args.address:
            print("Command {} requires an address argument.".format(args.command))
            sys.exit(64)
        if not re.match(r'^[^@]+@[^@?%:/&=]+.[^@?%:/&=]+$', args.address):
            print("{} is not a valid email address.".format(args.address))
            sys.exit(65)

    config = configparser.ConfigParser()
    config.read(args.config)

    # unfortunately connections and cursors do not support with-contexts
    cnx = mysql.connector.connect(**config['database'])
    result = COMMANDS[args.command](cnx, args.address)
    cnx.close()
    sys.exit(result)
