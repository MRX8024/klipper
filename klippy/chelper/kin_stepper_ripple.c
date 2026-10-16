// Stepper position correction for motor torque ripple
//
// Copyright (C) 2026  Maksim Bolgov <maksim8024@gmail.com>
//
// This file may be distributed under the terms of the GNU GPLv3 license.

#include <math.h> // cos
#include <stddef.h> // offsetof
#include <stdlib.h> // malloc
#include <string.h> // memset
#include "compiler.h" // __visible
#include "itersolve.h" // struct stepper_kinematics
#include "trapq.h" // struct move

#define MAX_HARMONIC 4
// Time span used to calculate the stepper velocity
#define VELOCITY_TIME 0.0005
// Square of the velocity in mm/s below which the direction is blended
#define DIRECTION_SMOOTH_V2 4.
// Maximum speed nodes of a harmonic
#define MAX_NODES 16

struct stepper_ripple {
    struct stepper_kinematics sk;
    struct stepper_kinematics *orig_sk, *kin_sk;
    double origin, inv_period;
    int count;
    int node_count[MAX_HARMONIC];
    double node_v[MAX_HARMONIC][MAX_NODES];
    double node_pos[MAX_HARMONIC][MAX_NODES][2];
    double node_neg[MAX_HARMONIC][MAX_NODES][2];
};

// Return the stepper velocity
static double
stepper_ripple_velocity(struct stepper_ripple *rs, struct move *m
                        , double move_time)
{
    double t0 = move_time - VELOCITY_TIME, t1 = move_time + VELOCITY_TIME;
    if (t0 < 0.)
        t0 = 0.;
    if (t1 > m->move_t)
        t1 = m->move_t;
    if (t1 - t0 < .000000001)
        return 0.;
    struct stepper_kinematics *ks = rs->kin_sk;
    return ((ks->calc_position_cb(ks, m, t1)
             - ks->calc_position_cb(ks, m, t0)) / (t1 - t0));
}

// Interpolate the speed nodes of harmonic index 'i' - stores the cos and
// sin coefficients of the positive and the negative direction
static void
stepper_ripple_nodes(struct stepper_ripple *rs, int i, double speed
                     , double *pos, double *neg)
{
    int n = rs->node_count[i], j0 = 0, j1 = 0;
    double *nv = rs->node_v[i], f = 0.;
    if (speed >= nv[n-1]) {
        j0 = j1 = n - 1;
    } else if (speed > nv[0]) {
        // Binary search of the nodes around the speed
        j0 = 0;
        j1 = n - 1;
        while (j1 - j0 > 1) {
            int m = (j0 + j1) / 2;
            if (speed < nv[m])
                j1 = m;
            else
                j0 = m;
        }
        f = (speed - nv[j0]) / (nv[j1] - nv[j0]);
    }
    int c;
    for (c = 0; c < 2; c++) {
        pos[c] = (rs->node_pos[i][j0][c]
                  + f * (rs->node_pos[i][j1][c] - rs->node_pos[i][j0][c]));
        neg[c] = (rs->node_neg[i][j0][c]
                  + f * (rs->node_neg[i][j1][c] - rs->node_neg[i][j0][c]));
    }
}

// Calculate the position correction at a given stepper position
static double
stepper_ripple_offset(struct stepper_ripple *rs, double pos, double velocity)
{
    double direction = velocity / sqrt(velocity * velocity
                                       + DIRECTION_SMOOTH_V2);
    double speed = fabs(velocity);
    double cycles = (pos - rs->origin) * rs->inv_period;
    double phase = 2. * M_PI * (cycles - floor(cycles));
    double c1 = cos(phase), s1 = sin(phase), ck = c1, sk = s1, res = 0.;
    int i;
    for (i = 0; i < rs->count; i++) {
        if (rs->node_count[i]) {
            // Blend the directions, only near zero speed both matter
            double cp[2], cn[2], w = .5 * (1. + direction);
            stepper_ripple_nodes(rs, i, speed, cp, cn);
            double a = w * cp[0] + (1. - w) * cn[0];
            double b = w * cp[1] + (1. - w) * cn[1];
            res += a * ck - b * sk;
        }
        // cos and sin of (i + 2) * phase
        double ck_next = ck * c1 - sk * s1;
        sk = sk * c1 + ck * s1;
        ck = ck_next;
    }
    return res;
}

static double
stepper_ripple_calc_position(struct stepper_kinematics *sk, struct move *m
                             , double move_time)
{
    struct stepper_ripple *rs = container_of(sk, struct stepper_ripple, sk);
    double pos = rs->orig_sk->calc_position_cb(rs->orig_sk, m, move_time);
    if (!rs->count)
        return pos;
    double velocity = stepper_ripple_velocity(rs, m, move_time);
    return pos + stepper_ripple_offset(rs, pos, velocity);
}

// A callback that forwards post_cb call to the original kinematics
static void
stepper_ripple_commanded_pos_post_fixup(struct stepper_kinematics *sk)
{
    struct stepper_ripple *rs = container_of(sk, struct stepper_ripple, sk);
    rs->orig_sk->commanded_pos = sk->commanded_pos;
    rs->orig_sk->post_cb(rs->orig_sk);
    sk->commanded_pos = rs->orig_sk->commanded_pos;
}

// Copy the step generation settings of the wrapped kinematics
void __visible
stepper_ripple_update_sk(struct stepper_kinematics *sk)
{
    struct stepper_ripple *rs = container_of(sk, struct stepper_ripple, sk);
    struct stepper_kinematics *orig_sk = rs->orig_sk;
    rs->sk.active_flags = orig_sk->active_flags;
    rs->sk.gen_steps_pre_active = orig_sk->gen_steps_pre_active;
    rs->sk.gen_steps_post_active = orig_sk->gen_steps_post_active;
}

// Wrap 'orig_sk', the velocity is calculated with 'kin_sk', the kinematics
// without input shaping
int __visible
stepper_ripple_set_sk(struct stepper_kinematics *sk
                      , struct stepper_kinematics *orig_sk
                      , struct stepper_kinematics *kin_sk)
{
    struct stepper_ripple *rs = container_of(sk, struct stepper_ripple, sk);
    if (!orig_sk->calc_position_cb || !kin_sk->calc_position_cb)
        return -1;
    rs->orig_sk = orig_sk;
    rs->kin_sk = kin_sk;
    rs->sk.calc_position_cb = stepper_ripple_calc_position;
    stepper_ripple_update_sk(sk);
    rs->sk.commanded_pos = orig_sk->commanded_pos;
    rs->sk.last_flush_time = orig_sk->last_flush_time;
    rs->sk.last_move_time = orig_sk->last_move_time;
    if (orig_sk->post_cb)
        rs->sk.post_cb = stepper_ripple_commanded_pos_post_fixup;
    return 0;
}

void __visible
stepper_ripple_set_phase(struct stepper_kinematics *sk, double origin
                         , double period)
{
    struct stepper_ripple *rs = container_of(sk, struct stepper_ripple, sk);
    rs->origin = origin;
    rs->inv_period = 1. / period;
}

// Update the number of used harmonics
static void
stepper_ripple_update_count(struct stepper_ripple *rs)
{
    int count = 0, i;
    for (i = 0; i < MAX_HARMONIC; i++)
        if (rs->node_count[i])
            count = i + 1;
    rs->count = count;
}

// Set the speed nodes of harmonic 'k': 'count' increasing speeds in mm/s
// with the cos and sin coefficients in mm of the positive and the negative
// direction of motion, 'count' of 0 clears them
int __visible
stepper_ripple_set_nodes(struct stepper_kinematics *sk, int k, int count
                         , double speeds[], double pos_cos[]
                         , double pos_sin[], double neg_cos[]
                         , double neg_sin[])
{
    struct stepper_ripple *rs = container_of(sk, struct stepper_ripple, sk);
    if (k < 1 || k > MAX_HARMONIC || count < 0 || count > MAX_NODES)
        return -1;
    int i;
    for (i = 1; i < count; i++)
        if (speeds[i] <= speeds[i-1])
            return -1;
    for (i = 0; i < count; i++) {
        rs->node_v[k-1][i] = speeds[i];
        rs->node_pos[k-1][i][0] = pos_cos[i];
        rs->node_pos[k-1][i][1] = pos_sin[i];
        rs->node_neg[k-1][i][0] = neg_cos[i];
        rs->node_neg[k-1][i][1] = neg_sin[i];
    }
    rs->node_count[k-1] = count;
    stepper_ripple_update_count(rs);
    return 0;
}

struct stepper_kinematics * __visible
stepper_ripple_alloc(void)
{
    struct stepper_ripple *rs = malloc(sizeof(*rs));
    memset(rs, 0, sizeof(*rs));
    rs->inv_period = 1.;
    return &rs->sk;
}
