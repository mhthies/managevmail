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


# #####################################################################
# Helper functions
# #####################################################################

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


def query_database(db, query, data=()):
    """
    Helper function to query MySQL database. This basically wraps cursor.execute(query, data), but takes care of
    creating and closing the connector and fetching the data.

    :param db: The MySQL Connector object to use for the query
    :param query: The SQL query
    :param data: The data to be filled into the query. See documentation of mysql.connector library for more information
    :return: The query result as a list of namedtuples or None if the query didn't produce rows
    :rtype: [collections.namedtuple] or None
    """
    cursor = db.cursor(named_tuple=True)
    cursor.execute(query, data)
    if cursor.with_rows:
        result = cursor.fetchall()
    else:
        result = None
    cursor.close()
    return result


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


def check_quota_usage(account_name):
    """
    Use doveadm to get the current quota usage of the account with given name.

    :param account_name: The name (email address) of the account to check
    :type account_name: str
    :return: The quota usage in MiB or None if it could not be found
    :rtype: float or None
    """
    result = subprocess.run(['doveadm', '-f', 'tab', 'quota', 'get', '-u', account_name], stdout=subprocess.PIPE,
                            universal_newlines=True)
    if result.returncode == 67:
        # Account does not exist (or similar error)
        return None
    result.check_returncode()
    value = int(result.stdout.split('\n')[1].split('\t')[2]) / 1024
    return value


def delete_mailbox(domain, user):
    """
    Delete the dovecot mailbox located at /var/vmail/mailboxes/<domain>/<user>/.

    :param domain: The domain
    :param user: The user
    """
    mailbox = os.path.join('/var/vmail/mailboxes', domain, user)
    if os.path.exists(mailbox):
        shutil.rmtree(mailbox)
    sieves = os.path.join('/var/vmail/sieve', domain, user)
    if os.path.exists(sieves):
        shutil.rmtree(sieves)


# #####################################################################
# User dialog functions for different commands
# #####################################################################

def list_accounts(db, _):
    result = query_database(db,
                            "SELECT `username`, `domain`, NULL AS `target_username`, NULL AS `target_domain`,"
                            "`enabled`, `sendonly` "
                            "FROM `accounts` "
                            "UNION SELECT `source_username`, `source_domain`, `destination_username`,"
                            "`destination_domain`, `enabled`, NULL "
                            "FROM `aliases`"
                            "ORDER BY `domain`, `username`")
    for account in result:
        print("{}{:>15}@{}{}{}".format("[dis] " if not account.enabled else "      ",
                                       account.username, account.domain,
                                       "\t→ {}@{}".format(account.target_username, account.target_domain)
                                       if account.target_username else "",
                                       "\t[sendonly]" if account.sendonly else ""))
    return 0


def show_account(db, account_name):
    user, domain = account_name.split('@')
    # First, show alias
    alias_result = query_database(db, "SELECT `destination_username`, `destination_domain`, `enabled` "
                                      "FROM `aliases` "
                                      "WHERE `source_username` = %s AND `source_domain` = %s",
                                  (user, domain))
    if alias_result:
        current_alias = alias_result[0]
        print("<{}> is an alias:\n"
              "Destination: <{}@{}>\n"
              "Enabled:     {}".format(account_name, current_alias.destination_username,
                                       current_alias.destination_domain,
                                       "Yes" if current_alias.enabled else "No"))

    # Now, show account
    account_result = query_database(db, "SELECT `enabled`, `quota`, `sendonly` "
                                        "FROM `accounts` "
                                        "WHERE `username` = %s AND `domain` = %s",
                                    (user, domain))
    if account_result:
        current_account = account_result[0]
        quota_used = check_quota_usage(account_name)

        print("{}<{}> is {}an account:\n"
              "Enabled:     {}\n"
              "Sendonly:    {}\n"
              "Quota:       {} MiB\n"
              "Quota used:  {}".format("\n" if alias_result else "",
                                       account_name,
                                       "also " if alias_result else "",
                                       "Yes" if current_account.enabled else "No",
                                       "Yes" if current_account.sendonly else "No",
                                       current_account.quota,
                                       "{:.1f} MiB ({:.1f} %)".format(quota_used, quota_used/current_account.quota*100)
                                       if quota_used is not None else "N/A"))

    if not alias_result and not account_result:
        print("<{}> is neither an account nor an alias.".format(account_name))


def add_account(db, account_name):
    # Check if name is already an account or alias
    user, domain = account_name.split('@')
    result = query_database(db, "SELECT COUNT(*) AS c FROM `accounts` WHERE `username` = %s AND `domain` = %s",
                            (user, domain))
    if result[0].c > 0:
        print("The account {} exists already.".format(account_name))
        return 2
    result = query_database(db, "SELECT `destination_username`, `destination_domain` "
                                "FROM `aliases` "
                                "WHERE `source_username` = %s AND `source_domain` = %s",
                            (user, domain))
    if result:
        current_alias = result[0]
        print("Warning: This address is currently an alias of {}@{}.".format(current_alias.destination_username,
                                                                             current_alias.destination_domain))
        if not query_user("Do you still want to create an account at the address?", bool, False):
            return 0

    # Check if domain exists
    result = query_database(db, "SELECT COUNT(*) AS c FROM `domains` WHERE `domain` = %s",
                            (domain,))

    if result[0].c != 1:
        print("The domain {} is not registered as virtual mail domain yet. Please add it manually to the database"
              .format(domain))
        return 2

    # Ask user for information
    pass1 = query_user("New account's password:", str, hide=True)
    if not pass1:
        print("Password must not be empty.")
        return 64
    pass2 = query_user("Type password again:", str, hide=True)
    if pass1 != pass2:
        print("Passwords do not match.")
        return 64
    enabled = query_user("Enable Account?", bool, True)
    send_only = query_user("Create send-only account?", bool, False)
    if not send_only:
        quota = query_user("Storage quota in MB:", int, 128)
    else:
        quota = 0

    # Hash password and create account
    pass_hash = hash_pw(pass1)
    query_database(db, "INSERT INTO `accounts` (`username`, `domain`, `password`, `quota`, `enabled`, `sendonly`) "
                       "VALUES(%s,%s,%s,%s,%s,%s)",
                   (user, domain, pass_hash, quota, enabled, send_only))
    db.commit()
    print("Account has been created.")
    return 0


def change_account(db, account_name):
    # Get current settings and exit if accounts doesn't exist
    user, domain = account_name.split('@')
    result = query_database(db, "SELECT `id`, `username`, `domain`, `enabled`, `quota`, `sendonly` "
                                "FROM `accounts` "
                                "WHERE `username` = %s AND `domain` = %s",
                            (user, domain))
    if not result:
        print("This account does not exist yet.")
        return 2
    current_account = result[0]

    # Query user for new values
    enabled = query_user("Account enabled?", bool, bool(current_account.enabled))
    send_only = query_user("Send-only account?", bool, bool(current_account.sendonly))
    if not send_only:
        quota = query_user("Quota in MB:", int, current_account.quota)
    else:
        quota = 0

    # Store new values
    query_database(db, "UPDATE `accounts` SET `enabled` = %s, `quota` = %s, `sendonly` = %s WHERE `id` = %s",
                   (enabled, quota, send_only, current_account.id))
    print("Stored new values.")
    db.commit()

    # Ask user, if mailbox shall be deleted
    if not send_only and current_account.sendonly:
        if query_user("Do you want to delete the accounts's mailbox?", bool, False):
            delete_mailbox(domain, user)
            print("Account's Mailbox has been deleted.")

    return 0


def change_password(db, account_name):
    # Get id and exit if accounts doesn't exist
    user, domain = account_name.split('@')
    result = query_database(db, "SELECT `id` FROM `accounts` WHERE `username` = %s AND `domain` = %s",
                            (user, domain))
    if not result:
        print("This account does not exist.")
        return 2
    current_account = result[0]

    # Query user for new password
    pass1 = query_user("New password:", str, hide=True)
    if not pass1:
        print("Password must not be empty.")
        return 64
    pass2 = query_user("Type password again:", str, hide=True)
    if pass1 != pass2:
        print("Passwords do not match.")
        return 64

    # Hash password and create account
    pass_hash = hash_pw(pass1)

    # Hash password and store new hash
    query_database(db, "UPDATE `accounts` SET `password` = %s WHERE `id` = %s",
                   (pass_hash, current_account.id))
    print("Stored new password.")
    db.commit()


def delete_account(db, account_name):
    # Get id and exit if accounts doesn't exist
    user, domain = account_name.split('@')
    result = query_database(db, "SELECT `id` FROM `accounts` WHERE `username` = %s AND `domain` = %s",
                            (user, domain))
    if not result:
        print("This account does not exist.")
        return 2
    current_account = result[0]

    if not query_user("Do you really want to delete the account {}?".format(account_name), bool, False):
        return 0

    # Delete database entry
    query_database(db, "DELETE FROM `accounts` WHERE `id` = %s", (current_account.id,))
    print("Account has been deleted.")
    db.commit()

    # Ask user, if mailbox shall be deleted
    if query_user("Do you want to delete the user's mailbox?", bool, False):
        delete_mailbox(domain, user)
    print("Account's Mailbox has been deleted.")

    return 0


def add_alias(db, alias_name):
    # Check if name is already an account or alias
    user, domain = alias_name.split('@')
    result = query_database(db, "SELECT `destination_username`, `destination_domain` "
                                "FROM `aliases` "
                                "WHERE `source_username` = %s AND `source_domain` = %s",
                            (user, domain))
    if result:
        current_alias = result[0]
        print("This address is already an alias of {}@{}.".format(current_alias.destination_username,
                                                                  current_alias.destination_domain))
        return 2
    result = query_database(db, "SELECT COUNT(*) AS c FROM `accounts` WHERE `username` = %s AND `domain` = %s",
                            (user, domain))
    if result[0].c > 0:
        print("There is already an account for address {}.".format(alias_name))
        if not query_user("Do you still want to create an alias at the address?", bool, False):
            return 0

    # Check if domain exists
    result = query_database(db, "SELECT COUNT(*) AS c FROM `domains` WHERE `domain` = %s", (domain,))
    if result[0].c == 0:
        print("The domain {} is not registered as virtual mail domain yet. Please add it manually to the database"
              .format(domain))
        return 2

    # Ask user for information
    while True:
        target = query_user("Destination address:", str)
        if re.match(r'^[^@]+@[^@?%:/&=]+.[^@?%:/&=]+$', args.address):
            break
        else:
            print("'{}' is not a valid target email address. Please try again.".format(target))
    target_user, target_domain = target.strip().split('@')
    enabled = query_user("Enable Alias?", bool, True)

    # Create new alias
    query_database(db, "INSERT INTO `aliases` (`source_username`, `source_domain`, `destination_username`, "
                       "`destination_domain`, `enabled`) "
                       "VALUES(%s,%s,%s,%s,%s)",
                   (user, domain, target_user, target_domain, enabled))
    db.commit()
    print("Alias has been created.")
    return 0


def change_alias(db, alias_name):
    # Get current data
    user, domain = alias_name.split('@')
    result = query_database(db, "SELECT `id`, `destination_username`, `destination_domain`, `enabled` "
                                "FROM `aliases` "
                                "WHERE `source_username` = %s AND `source_domain` = %s",
                            (user, domain))
    if not result:
        print("{} is currently not registered as alias.".format(alias_name))
        return 2
    current_alias = result[0]

    # Ask user for information
    while True:
        target = query_user("New destination address:", str, "{}@{}".format(current_alias.destination_username,
                                                                            current_alias.destination_domain))
        if re.match(r'^[^@]+@[^@?%:/&=]+.[^@?%:/&=]+$', args.address):
            break
        else:
            print("'{}' is not a valid target email address. Please try again.".format(target))
    target_user, target_domain = target.strip().split('@')
    enabled = query_user("Enable Alias?", bool, current_alias.enabled)

    # Store new values
    query_database(db, "UPDATE `aliases` SET  `enabled` = %s, `destination_username` = %s, `destination_domain` = %s "
                       "WHERE `id` = %s",
                   (enabled, target_user, target_domain, current_alias.id))
    print("Stored new values.")
    db.commit()


def delete_alias(db, alias_name):
    # Get current data
    user, domain = alias_name.split('@')
    result = query_database(db, "SELECT `id`, `destination_username`, `destination_domain`, `enabled` "
                                "FROM `aliases` "
                                "WHERE `source_username` = %s AND `source_domain` = %s",
                            (user, domain))
    if not result:
        print("{} is currently not registered as alias.".format(alias_name))
        return 2
    current_alias = result[0]

    # Ask user for confirmation
    print("The alias is {} → {}@{}".format(alias_name, current_alias.destination_domain,
                                           current_alias.destination_username))
    if not query_user("Do you really want to delete it?", bool, False):
        return 0

    # Store new values
    query_database(db, "DELETE FROM `aliases` WHERE `id` = %s", (current_alias.id,))
    print("Alias has been deleted.")
    db.commit()


# #####################################################################
# Main script
# #####################################################################
# Cli argument parsing, config file parsing and database connection happens here

# Map cli commands to handler functions
COMMANDS = {
    'list': list_accounts,
    'show': show_account,
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
