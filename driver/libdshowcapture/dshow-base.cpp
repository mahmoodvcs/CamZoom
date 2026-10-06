/*
 *  Copyright (C) 2014 Lain Bailey <lain@obsproject.com>
 *
 *  This library is free software; you can redistribute it and/or
 *  modify it under the terms of the GNU Lesser General Public
 *  License as published by the Free Software Foundation; either
 *  version 2.1 of the License, or (at your option) any later version.
 *
 *  This library is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 *  Lesser General Public License for more details.
 *
 *  You should have received a copy of the GNU Lesser General Public
 *  License along with this library; if not, write to the Free Software
 *  Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301
 *  USA
 */

/* Modified for CamZoom: trimmed to the one function the virtual camera filter uses. */

#include "dshow-base.hpp"

namespace DShow {

wstring ConvertHRToEnglish(HRESULT hr)
{
	LPWSTR buffer = NULL;
	wstring str;

	FormatMessageW(FORMAT_MESSAGE_FROM_SYSTEM |
			       FORMAT_MESSAGE_ALLOCATE_BUFFER |
			       FORMAT_MESSAGE_IGNORE_INSERTS,
		       NULL, hr, MAKELANGID(LANG_ENGLISH, SUBLANG_ENGLISH_US),
		       (LPTSTR)&buffer, 0, NULL);

	if (buffer) {
		str = buffer;
		LocalFree(buffer);
	}

	return str;
}

}; /* namespace DShow */
