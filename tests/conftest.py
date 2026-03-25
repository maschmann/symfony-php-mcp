"""
Shared fixtures for symfony-php-mcp tests.

Creates a minimal fake Symfony project on disk so tests run without needing
a real Symfony installation or PHP binary.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Minimal fake Symfony project
# ---------------------------------------------------------------------------

COMPOSER_JSON = {
    "name": "acme/shop",
    "description": "Test Symfony project",
    "require": {
        "php": ">=8.2",
        "symfony/framework-bundle": "^7.0",
        "symfony/twig-bundle": "^7.0",
        "doctrine/orm": "^3.0",
        "symfony/security-bundle": "^7.0",
    },
    "require-dev": {
        "symfony/maker-bundle": "^1.50",
        "phpunit/phpunit": "^10.0",
    },
    "autoload": {
        "psr-4": {"App\\": "src/"}
    },
    "scripts": {
        "test": "phpunit",
        "lint": "php-cs-fixer fix --dry-run",
    },
}

SYMFONY_LOCK = {
    "symfony/framework-bundle": {"versions": ["7.1.3"]},
    "symfony/http-kernel": {"versions": ["7.1.3"]},
}

SERVICES_YAML = textwrap.dedent("""\
    services:
        _defaults:
            autowire: true
            autoconfigure: true
            public: false

        App\\:
            resource: '../src/'
            exclude:
                - '../src/DependencyInjection/'
                - '../src/Entity/'
                - '../src/Kernel.php'

        App\\Service\\UserService:
            class: App\\Service\\UserService
            arguments:
                $mailer: '@mailer'
            tags:
                - { name: 'app.service' }

        App\\Repository\\UserRepository:
            class: App\\Repository\\UserRepository
            public: true
""")

USER_CONTROLLER_PHP = textwrap.dedent("""\
    <?php

    namespace App\\Controller;

    use Symfony\\Bundle\\FrameworkBundle\\Controller\\AbstractController;
    use Symfony\\Component\\HttpFoundation\\Response;
    use Symfony\\Component\\Routing\\Annotation\\Route;
    use App\\Service\\UserService;

    /**
     * Controller for user-related actions.
     * This is a doc-block comment that should be strippable.
     */
    #[Route('/user', name: 'app_user_')]
    class UserController extends AbstractController
    {
        public function __construct(
            private readonly UserService $userService,
        ) {}

        #[Route('/', name: 'index', methods: ['GET'])]
        public function index(): Response
        {
            $users = $this->userService->findAll();
            return $this->render('user/index.html.twig', ['users' => $users]);
        }

        #[Route('/{id}', name: 'show', methods: ['GET'])]
        public function show(int $id): Response
        {
            $user = $this->userService->find($id);
            return $this->render('user/show.html.twig', ['user' => $user]);
        }

        #[Route('/{id}/edit', name: 'edit', methods: ['GET', 'POST'])]
        public function edit(int $id): Response
        {
            /* This is an inline block comment */
            $user = $this->userService->find($id);
            return $this->render('user/edit.html.twig', ['user' => $user]);
        }

        protected function privateHelper(): string
        {
            return 'helper';
        }
    }
""")

USER_SERVICE_PHP = textwrap.dedent("""\
    <?php

    namespace App\\Service;

    use App\\Repository\\UserRepository;

    class UserService
    {
        public function __construct(
            private readonly UserRepository $repo,
        ) {}

        public function findAll(): array
        {
            return $this->repo->findAll();
        }

        public function find(int $id): ?object
        {
            return $this->repo->find($id);
        }
    }
""")

USER_INTERFACE_PHP = textwrap.dedent("""\
    <?php

    namespace App\\Contract;

    interface UserProviderInterface
    {
        public function findAll(): array;
        public function find(int $id): ?object;
    }
""")

USER_REPOSITORY_PHP = textwrap.dedent("""\
    <?php

    namespace App\\Repository;

    use Doctrine\\Bundle\\DoctrineBundle\\Repository\\ServiceEntityRepository;

    class UserRepository extends ServiceEntityRepository
    {
        public function findAll(): array
        {
            return $this->findBy([]);
        }
    }
""")

BASE_TWIG = textwrap.dedent("""\
    <!DOCTYPE html>
    <html>
    <head><title>{% block title %}My App{% endblock %}</title></head>
    <body>
    {% block body %}{% endblock %}
    {% block scripts %}{% endblock %}
    </body>
    </html>
""")

USER_INDEX_TWIG = textwrap.dedent("""\
    {% extends 'base.html.twig' %}

    {% block title %}Users{% endblock %}

    {% block body %}
    {% include '_partials/breadcrumb.html.twig' %}
    <ul>
    {% for user in users %}
        <li>{{ user.name }}</li>
    {% endfor %}
    </ul>
    {% endblock %}
""")

BREADCRUMB_TWIG = textwrap.dedent("""\
    <nav>
    {% for item in breadcrumbs %}
        <a href="{{ item.url }}">{{ item.label }}</a>
    {% endfor %}
    </nav>
""")


@pytest.fixture()
def symfony_project(tmp_path: Path) -> Path:
    """Create a minimal fake Symfony project under tmp_path and return its root."""
    root = tmp_path / "acme-shop"
    root.mkdir()

    # composer.json + symfony.lock
    (root / "composer.json").write_text(json.dumps(COMPOSER_JSON, indent=2))
    (root / "symfony.lock").write_text(json.dumps(SYMFONY_LOCK, indent=2))

    # .env
    (root / ".env").write_text("APP_ENV=dev\nAPP_SECRET=testsecret\n")

    # config/services.yaml
    (root / "config").mkdir()
    (root / "config" / "services.yaml").write_text(SERVICES_YAML)

    # src/ PHP files
    (root / "src" / "Controller").mkdir(parents=True)
    (root / "src" / "Service").mkdir(parents=True)
    (root / "src" / "Repository").mkdir(parents=True)
    (root / "src" / "Contract").mkdir(parents=True)

    (root / "src" / "Controller" / "UserController.php").write_text(USER_CONTROLLER_PHP)
    (root / "src" / "Service" / "UserService.php").write_text(USER_SERVICE_PHP)
    (root / "src" / "Repository" / "UserRepository.php").write_text(USER_REPOSITORY_PHP)
    (root / "src" / "Contract" / "UserProviderInterface.php").write_text(USER_INTERFACE_PHP)

    # templates/
    (root / "templates").mkdir()
    (root / "templates" / "_partials").mkdir()
    (root / "templates" / "user").mkdir()

    (root / "templates" / "base.html.twig").write_text(BASE_TWIG)
    (root / "templates" / "user" / "index.html.twig").write_text(USER_INDEX_TWIG)
    (root / "templates" / "_partials" / "breadcrumb.html.twig").write_text(BREADCRUMB_TWIG)

    # bin/console placeholder (not executable, just present)
    (root / "bin").mkdir()
    (root / "bin" / "console").write_text("#!/usr/bin/env php\n<?php // placeholder\n")

    return root
