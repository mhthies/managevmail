
# Command line management tool for vmail database

This Python script aims to provide a simple command line interface to manage virtual mail accounts in an MySQL database
that is organized as explained in the great tutorial of Thomas Leister:

https://thomas-leister.de/mailserver-debian-stretch/

The database layout, he is proposing, includes a table `accounts` with information (name, password hash, quota, enabled
flag, sendonly flag) on virtual mail accounts to be used by dovecot and a table `aliases` to be used by postfix for
redirects. Managing them is somehow compilcated, as it requires dealing with cumbersome SQL queries and the additional
usage of the `doveadm` command to hash passwords.

This script provides simple and dialog-oriented command line commands to get a quick overview over accounts and aliases
and modify them.

Management of the `domains` and `tlspolicies` tables must (for now) still be done manually, which should be okay, since
it is required rather rarely.


## Install

You need Python 3 and the Python 3-MySQL-Connector:

```
$ apt install python3-mysql
```

Now download the script (or clone it out from GitHub) and move it to any place you like. Move the configuration file
`config.ini` to `/root/managevmail/config.ini` (which is the default location) or any other place you like (which then
requires the `-c` parameter to use the script).  Chmod the `config.ini` file, if neccessary, to be only readable for
root (as it q)

Create a MySQL user for the management and grant it SELECT, UPDATE, INSERT and DELETE privileges on the `vmail` database
or use the existing `vmail` user's credentials. Enter the MySQL credentials into the `config.ini`.


## Usage

The script requires root privileges to read the config.ini file, use the doveadm command and delete mailbox folders
after accounts deletions.

The basic syntax is:

```
$ managevmail.py [-c path/to/config.ini] COMMAND [ADDRESS]
```

The commands are:

| Command     | Description                                                                                                                                                |
|-------------|------------------------------------------------------------------------------------------------------------------------------------------------------------|
| list        | List all known accounts and aliases. Aliases are displays with their destination, disabled and sendonly addresses are flagged. Does not require an address |
| add         | Add an account. Queries interactively for quota, enabled flag, sendonly flag and password.                                                                 |
| change      | Change settings of an account. Queries interactively for quota, enabled flag and sendonly flag.                                                            |
| pw          | Change password of an account. Queries interactively for the new password.                                                                                 |
| delete      | Delete an account. Asks for confirmation, asks again for confirmation of mailbox deletion.                                                                 |
| addalias    | Add an alias. Queries interactively for enabled flag and destination address.                                                                              |
| changealias | Change settings of an alias. Queries interactively for enabled flag and destination address.                                                               |
| deletealias | Delete an alias. Asks for confirmation.                                                                                                                    |


### Examples

```
$ managevmail.py list
                  cms@exmaple.com   [sendonly]
                 info@example.com   → mail@example.com
                 mail@example.com
[dis]             old@example.com
$
$ managevmail.py addalias webmaster@example.com
Destination address: mail@example.com
Enable Alias? [Y/n] 
Alias has been created.
$
$ managevmail.py pw cms@example.com
New password: 
Type password again: 
Stored new password.
$
$ managevmail.py delete old@example.com
Do you really want to delete the account old@example.com? [y/N] y
Account has been deleted.
Do you want to delete the user's mailbox? [y/N] y
Account's Mailbox has been deleted.
$
```