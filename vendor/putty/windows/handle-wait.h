/*
 * PuTTY is copyright 1997-2026 Simon Tatham.
 *
 * Portions copyright Robert de Bath, Joris van Rantwijk, Delian
 * Delchev, Andreas Schultz, Jeroen Massar, Wez Furlong, Nicolas Barry,
 * Justin Bradford, Ben Harris, Malcolm Smith, Ahmad Khalifa, Markus
 * Kuhn, Colin Watson, Christopher Staite, Lorenz Diener, Christian
 * Brabandt, Jeff Smith, Pavel Kryukov, Maxim Kuznetsov, Svyatoslav
 * Kuzmich, Nico Williams, Viktor Dukhovni, Josh Dersch, Lars Brinkhoff,
 * and CORE SDI S.A.
 *
 * Permission is hereby granted, free of charge, to any person
 * obtaining a copy of this software and associated documentation files
 * (the "Software"), to deal in the Software without restriction,
 * including without limitation the rights to use, copy, modify, merge,
 * publish, distribute, sublicense, and/or sell copies of the Software,
 * and to permit persons to whom the Software is furnished to do so,
 * subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be
 * included in all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
 * EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
 * MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
 * NONINFRINGEMENT.  IN NO EVENT SHALL THE COPYRIGHT HOLDERS BE LIABLE
 * FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
 * CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
 * WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
 */

/* HANDLE 等待集合声明；平台提供 HANDLE 与 MAXIMUM_WAIT_OBJECTS。 */
#ifndef PUTTY_WINDOWS_HANDLE_WAIT_H
#define PUTTY_WINDOWS_HANDLE_WAIT_H

#include <stdint.h>

typedef struct HandleWait HandleWait;
typedef void (*handle_wait_callback_fn_t)(void *);
HandleWait *add_handle_wait(HANDLE h, handle_wait_callback_fn_t callback,
                            void *callback_ctx);
void delete_handle_wait(HandleWait *hw);

typedef struct HandleWaitList {
    HANDLE handles[MAXIMUM_WAIT_OBJECTS];
    uint64_t registrations[MAXIMUM_WAIT_OBJECTS];
    int nhandles;
} HandleWaitList;
HandleWaitList *get_handle_wait_list(void);
void handle_wait_activate(HandleWaitList *hwl, int index);
void handle_wait_list_free(HandleWaitList *hwl);

#endif
