# Kolla-Ansible host bootstrap excerpt

- URL: https://docs.openstack.org/kolla-ansible/2025.1/reference/deployment-and-bootstrapping/bootstrap-servers.html
- Locator: opening capability list and sections for hosts, users, packages, Docker storage, firewall, and NTP.
- Retrieved: 2026-08-20.

The versioned guide says `bootstrap-servers` configures hosts before container deployment through the `baremetal` Ansible role. It explicitly covers `/etc/hosts`, user/group/SSH/sudoers, package installation and removal, Docker storage driver and data-root, and only a generic NTP daemon configuration item; it does not name Chrony or document Chrony-specific automation. Its firewall section states that only Firewalld is supported; it does not document native nftables management.
