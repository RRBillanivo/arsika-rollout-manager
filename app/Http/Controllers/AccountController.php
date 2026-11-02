<?php

namespace App\Http\Controllers;

use App\Core\HttpException;
use App\Core\Request;
use App\Core\Response;
use App\Models\User;
use App\Services\SessionManager;
use App\Services\Validator;

/** The signed-in administrator's own account. */
final class AccountController extends Controller
{
    /** @var User */
    private $users;
    /** @var Validator */
    private $validator;
    /** @var SessionManager */
    private $session;

    public function __construct(User $users, Validator $validator, SessionManager $session)
    {
        $this->users = $users;
        $this->validator = $validator;
        $this->session = $session;
    }

    /** GET /api/v1/account */
    public function show(Request $request): Response
    {
        return Response::json(['user' => $this->users->toArray($this->user($request))]);
    }

    /** PUT /api/v1/account/password {current_password, password, password_confirmation}; other devices are signed out. */
    public function updatePassword(Request $request): Response
    {
        $user = $this->user($request);
        $errors = [];
        if (!$this->users->verifyPassword($user, $request->password('current_password'))) {
            $errors['current_password'] = trans('auth.confirm_wrong');
        }
        list($password, $error) = $this->validator->password($request->password('password'), (string)$user['username']);
        if ($error !== null) {
            $errors['password'] = $error;
        } elseif ($request->password('password_confirmation') !== $password) {
            $errors['password_confirmation'] = trans('validation.password_confirmation');
        }
        if ($errors) {
            throw HttpException::validation($errors);
        }
        $this->users->changePassword((int)$user['id'], $password);
        $this->session->refresh($this->users->find((int)$user['id']));
        $this->audit($request, 'password_changed', 'user:' . $user['id']);
        return Response::json(['message' => trans('account.password_changed')]);
    }
}
